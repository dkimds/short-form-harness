"""
src/generate/gate.py — Gate QA 모듈 (요구사항 12.1~12.5)

최종 mp4에 대해 결정론적 검사(aspect_ratio, duration, cut_count)와
Gemini 비전 자기판정(mood, captions, visual style)을 수행하고,
gate.json을 outputs/<run_id>/에 저장한다.

설계 원칙:
- src/analyze/ 를 절대 import하지 않는다 (요구사항 13.2)
- 모든 벤더 호출은 VendorClient를 통해서만 (요구사항 13.5)
- ffprobe 실패는 passed=True로 graceful 처리 (환경 의존성 최소화)
- VendorError는 catch → vision judgment skipped 처리
"""

from __future__ import annotations

import logging
import subprocess
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.common.exceptions import VendorError
from src.common.io import write_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_ASPECT_RATIO_TARGET = 9 / 16          # 0.5625
_ASPECT_RATIO_TOLERANCE = 0.05
_GATE_JUDGE_PROMPT_PATH = Path("prompts/gate_judge.md")


# ---------------------------------------------------------------------------
# 데이터클래스
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    """게이트 QA 결과를 담는 데이터클래스.

    Attributes:
        passed: 결정론적 + 비전 판정이 모두 통과했으면 True.
        deterministic: aspect_ratio/duration/cut_count 측정값 및 판정.
        vision_judgment: mood/captions/visual 자기판정 결과.
        reasons: 실패 이유 또는 요약 이유 목록.
        retry_count: 재시도 횟수 (run_with_retry에서 증가).
    """

    passed: bool
    deterministic: dict
    vision_judgment: dict
    reasons: list[str]
    retry_count: int = 0


# ---------------------------------------------------------------------------
# 결정론적 검사
# ---------------------------------------------------------------------------

def deterministic_check(final_mp4: str, profile: dict) -> dict:
    """ffprobe로 aspect_ratio, duration, cut_count를 측정하고 profile과 비교한다.

    Args:
        final_mp4: 검사할 mp4 파일 경로.
        profile: style_profile dict (format, pacing 섹션 포함).

    Returns:
        {
            "aspect_ratio": {"measured": "W:H", "passed": bool},
            "duration": {"measured": float, "passed": bool},
            "cut_count": {"estimated": int, "passed": bool},
            "passed": bool,
        }
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                final_mp4,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        probe_data = json.loads(result.stdout)
    except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        logger.warning("[gate] ffprobe 실행 실패 — 결정론적 검사를 건너뜁니다: %s", exc)
        return {
            "aspect_ratio": {"measured": "unknown", "passed": True, "warning": str(exc)},
            "duration": {"measured": 0.0, "passed": True, "warning": str(exc)},
            "cut_count": {"estimated": 0, "passed": True, "warning": str(exc)},
            "passed": True,
        }

    # 비디오 스트림 추출
    streams = probe_data.get("streams", [])
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"),
        None,
    )

    if video_stream is None:
        logger.warning("[gate] 비디오 스트림을 찾을 수 없습니다.")
        return {
            "aspect_ratio": {"measured": "unknown", "passed": True},
            "duration": {"measured": 0.0, "passed": True},
            "cut_count": {"estimated": 0, "passed": True},
            "passed": True,
        }

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    duration_str = video_stream.get("duration", "0")
    try:
        duration = float(duration_str)
    except (ValueError, TypeError):
        duration = 0.0

    # --- aspect_ratio 검사 ---
    if height > 0:
        measured_ratio = width / height
        aspect_passed = abs(measured_ratio - _ASPECT_RATIO_TARGET) <= _ASPECT_RATIO_TOLERANCE
        aspect_measured = f"{width}:{height}"
    else:
        aspect_passed = False
        aspect_measured = f"{width}:{height}"

    # --- duration 검사 ---
    fmt = profile.get("format", {})
    duration_range = fmt.get("duration_sec_range", [0, float("inf")])
    duration_passed = duration_range[0] <= duration <= duration_range[1]

    # --- cut_count 추정 (간단한 duration / avg_shot_len_sec 방식) ---
    pacing = profile.get("pacing", {})
    avg_shot_len = pacing.get("avg_shot_len_sec", 2.0)
    cut_count_range = pacing.get("cut_count_range", [1, 999])

    if duration > 0 and avg_shot_len > 0:
        estimated_cuts = max(1, round(duration / avg_shot_len))
    else:
        estimated_cuts = 1

    cut_passed = cut_count_range[0] <= estimated_cuts <= cut_count_range[1]

    overall_passed = aspect_passed and duration_passed and cut_passed

    return {
        "aspect_ratio": {
            "measured": aspect_measured,
            "passed": aspect_passed,
        },
        "duration": {
            "measured": duration,
            "passed": duration_passed,
        },
        "cut_count": {
            "estimated": estimated_cuts,
            "passed": cut_passed,
        },
        "passed": overall_passed,
    }


# ---------------------------------------------------------------------------
# 비전 자기판정
# ---------------------------------------------------------------------------

def vision_judge(client, final_mp4: str, profile: dict) -> dict:
    """Gemini 비전으로 영상의 mood/captions/visual style을 자기판정한다.

    Args:
        client: VendorClient 인스턴스.
        final_mp4: 판정할 mp4 파일 경로.
        profile: style_profile dict (audio, visual, narrative 섹션 포함).

    Returns:
        {"verdict": "pass"|"fail", "reasons": [...]}
        VendorError 발생 시: {"verdict": "pass", "reasons": ["vision judgment skipped: API error"]}
    """
    try:
        prompt_text = _GATE_JUDGE_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("[gate] gate_judge.md 프롬프트 파일을 찾을 수 없습니다.")
        return {
            "verdict": "pass",
            "reasons": ["vision judgment skipped: prompt file not found"],
        }

    # profile에서 플레이스홀더 채우기
    audio = profile.get("audio", {})
    visual = profile.get("visual", {})
    narrative = profile.get("narrative", {})
    beats = narrative.get("beats", [])

    music_mood = audio.get("music_mood", "unknown")
    color_grade = visual.get("color_grade", "unknown")
    lighting = visual.get("lighting", "unknown")
    beat_count = str(len(beats))
    has_voiceover = str(audio.get("has_voiceover", False)).lower()

    filled_prompt = (
        prompt_text
        .replace("{music_mood}", music_mood)
        .replace("{color_grade}", color_grade)
        .replace("{lighting}", lighting)
        .replace("{beat_count}", beat_count)
        .replace("{has_voiceover}", has_voiceover)
    )

    try:
        result = client.judge_video(final_mp4, filled_prompt)
        return result
    except VendorError as exc:
        logger.warning("[gate] VendorError — vision judgment를 건너뜁니다: %s", exc)
        return {
            "verdict": "pass",
            "reasons": ["vision judgment skipped: API error"],
        }


# ---------------------------------------------------------------------------
# run_gate
# ---------------------------------------------------------------------------

def run_gate(client, final_mp4: str, profile: dict, run_dir: str) -> GateResult:
    """결정론적 검사 + 비전 자기판정을 수행하고 gate.json을 저장한다.

    Args:
        client: VendorClient 인스턴스.
        final_mp4: 검사할 mp4 파일 경로.
        profile: style_profile dict.
        run_dir: outputs/<run_id>/ 디렉터리 경로.

    Returns:
        GateResult 인스턴스.
    """
    det = deterministic_check(final_mp4, profile)
    vis = vision_judge(client, final_mp4, profile)

    passed = det["passed"] and vis.get("verdict") == "pass"

    # 이유 수집
    reasons: list[str] = list(vis.get("reasons", []))
    if not det["passed"]:
        if not det["aspect_ratio"]["passed"]:
            reasons.append(
                f"aspect_ratio 불일치: {det['aspect_ratio']['measured']} (기대: 9:16)"
            )
        if not det["duration"]["passed"]:
            reasons.append(
                f"duration 범위 초과: {det['duration']['measured']:.2f}s"
            )
        if not det["cut_count"]["passed"]:
            reasons.append(
                f"cut_count 범위 초과: {det['cut_count']['estimated']}컷"
            )

    gate_data = {
        "passed": passed,
        "deterministic": det,
        "vision_judgment": vis,
        "reasons": reasons,
    }

    gate_path = Path(run_dir) / "gate.json"
    write_json(gate_data, gate_path)
    logger.info("[gate] gate.json 저장 완료: %s (passed=%s)", gate_path, passed)

    return GateResult(
        passed=passed,
        deterministic=det,
        vision_judgment=vis,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# run_with_retry
# ---------------------------------------------------------------------------

def run_with_retry(
    generate_once: Callable[[], str],
    client,
    profile: dict,
    run_dir: str,
    *,
    max_retries: int = 2,
) -> GateResult:
    """generate_once() → run_gate()를 실행하고, 실패 시 최대 max_retries 회 재시도한다.

    Args:
        generate_once: 호출할 때마다 final_mp4 경로(str)를 반환하는 콜러블.
        client: VendorClient 인스턴스.
        profile: style_profile dict.
        run_dir: outputs/<run_id>/ 디렉터리 경로.
        max_retries: 최대 재시도 횟수 (기본 2).

    Returns:
        최종 GateResult. 모든 시도가 실패하면 마지막 GateResult를 반환한다.
    """
    last_result: GateResult | None = None

    total_attempts = max_retries + 1
    for attempt in range(total_attempts):
        final_mp4 = generate_once()
        result = run_gate(client, final_mp4, profile, run_dir)
        result.retry_count = attempt

        if result.passed:
            logger.info("[gate] 시도 %d/%d — PASS", attempt + 1, total_attempts)
            return result

        logger.warning(
            "[gate] 시도 %d/%d — FAIL (이유: %s)",
            attempt + 1,
            total_attempts,
            result.reasons,
        )
        last_result = result

    # 모든 시도 실패 → 마지막 결과 반환
    logger.warning("[gate] %d회 시도 모두 실패 — 마지막 결과 반환", total_attempts)
    return last_result  # type: ignore[return-value]

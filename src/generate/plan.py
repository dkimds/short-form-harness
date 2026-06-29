"""
src/generate/plan.py — 숏리스트 플래닝 (요구사항 9.1~9.4, 16.4)

beat_sheet를 순회해 각 장면의 숏 계획을 수립하고 shotlist.json으로 저장한다.

핵심 설계 원칙:
- narrative.beats를 순서대로 순회해 shot_type·duration·asset_type·prompt를 결정
- pacing.cut_count_range에서 rng로 총 컷 수 샘플링
- product_hero beats → P0 에서는 'imagen_image' (P1에서 'veo_i2v')
- 나머지 모든 beats → 'imagen_image'
- 비트 ↔ 컷 수 배분: 비율(duration) 기반, 각 beat 최소 1컷 보장
- src/analyze/ 를 절대 import하지 않는다 (요구사항 13.2)
- 벤더 호출 없음 — 이 모듈은 순수 로직 레이어
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

from src.common.io import write_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _distribute_cuts(beats: list[dict], total_cuts: int) -> list[int]:
    """비트 배열에 총 컷 수를 비율 기반으로 배분한다.

    알고리즘:
    1. 각 beat에 최소 1컷 할당
    2. 남은 컷(total_cuts - num_beats)을 beat 길이(duration) 내림차순으로 배분
       → 길이가 긴 beat에 우선 배분
    3. total_cuts < num_beats인 경우 모든 beat에 1컷씩 (최소 보장)

    Args:
        beats: narrative.beats 배열 (start_sec, end_sec 필드 필요)
        total_cuts: 총 컷 수 (pacing.cut_count_range에서 샘플링된 값)

    Returns:
        각 beat에 할당된 컷 수 리스트 (beats와 같은 순서)
    """
    num_beats = len(beats)
    if num_beats == 0:
        return []

    # 각 beat의 길이 계산
    durations = [
        max(0.0, beat.get("end_sec", 0.0) - beat.get("start_sec", 0.0))
        for beat in beats
    ]
    total_duration = sum(durations)

    # 모든 beat에 최소 1컷 할당
    cuts = [1] * num_beats

    # total_cuts가 beat 수보다 작으면 최소 1컷/beat 그대로
    remainder = total_cuts - num_beats
    if remainder <= 0:
        return cuts

    # 남은 컷을 길이 내림차순 beat 인덱스 순으로 배분
    if total_duration > 0:
        # 비율 기반 정렬: duration이 길수록 먼저
        indexed_durations = sorted(
            enumerate(durations), key=lambda x: x[1], reverse=True
        )
    else:
        # duration 정보 없으면 순서대로
        indexed_durations = list(enumerate(durations))

    for i in range(remainder):
        beat_idx = indexed_durations[i % num_beats][0]
        cuts[beat_idx] += 1

    return cuts


def _build_prompt_text(beat: dict, profile: dict, brief: dict) -> str:
    """단일 숏(shot)에 대한 Imagen 생성 프롬프트를 구성한다.

    형식:
    "9:16 vertical short-form beauty video frame: {shot_type}, {intent}.
     Style: {color_grade}, {lighting}. Subject: {product_subject}."

    Args:
        beat: narrative.beats 항목
        profile: style_profile dict
        brief: 생성 브리프 dict

    Returns:
        완성된 프롬프트 문자열
    """
    shot_type = beat.get("shot_type", "medium_shot")
    intent = beat.get("intent", "")

    visual = profile.get("visual", {})
    color_grade = visual.get("color_grade", "warm_soft_pastel")
    lighting = visual.get("lighting", "natural_window_soft")
    accent_color = visual.get("accent_color", "")

    user_input = brief.get("user_input", {})
    product_subject = user_input.get("value", "beauty product")
    if user_input.get("kind") in ("image", "video"):
        product_subject = Path(product_subject).stem if product_subject else "beauty product"

    # 악센트 색상이 있으면 스타일에 포함
    style_parts = [color_grade, lighting]
    if accent_color:
        style_parts.append(f"accent {accent_color}")
    style_str = ", ".join(style_parts)

    prompt = (
        f"9:16 vertical short-form beauty video frame: {shot_type}, {intent}. "
        f"Style: {style_str}. Subject: {product_subject}."
    )
    return prompt


# ---------------------------------------------------------------------------
# 퍼블릭 API
# ---------------------------------------------------------------------------

def build_shotlist(
    brief: dict,
    profile: dict,
    hook_text: str,
    *,
    rng: random.Random,
) -> dict:
    """narrative.beats를 순회해 숏 계획을 수립한다.

    pacing.cut_count_range에서 총 컷 수를 샘플링하고, 각 beat에 비율 기반으로
    컷 수를 배분한다. 각 beat 내에서 컷들은 beat 길이를 균등 분할한다.

    product_hero beats는 'veo_i2v'로 처리한다.
    나머지 모든 beats는 'imagen_image'다.

    Args:
        brief: build_brief()가 반환한 브리프 dict
        profile: style_profile dict
        hook_text: generate_hook()이 반환한 훅 텍스트 (프롬프트에 포함 가능)
        rng: 결정적 재현을 위해 외부에서 주입된 random.Random 인스턴스

    Returns:
        shotlist dict (run_id, shots 배열)
    """
    pacing = profile.get("pacing", {})
    cut_count_range = pacing.get("cut_count_range", [4, 12])
    cut_min = int(cut_count_range[0])
    cut_max = int(cut_count_range[1])

    # 총 컷 수 샘플링
    total_cuts = rng.randint(cut_min, cut_max)

    beats = profile.get("narrative", {}).get("beats", [])

    if not beats:
        logger.warning("[plan] narrative.beats가 비어 있습니다. 빈 숏리스트를 반환합니다.")
        run_id = Path(brief.get("run_dir", "outputs/unknown")).name if brief.get("run_dir") else "unknown"
        return {"run_id": run_id, "shots": []}

    # 비트별 컷 수 배분
    cuts_per_beat = _distribute_cuts(beats, total_cuts)

    # run_id 추출 (brief에 run_dir이 있으면 basename 사용)
    run_dir = brief.get("run_dir", "")
    run_id = Path(run_dir).name if run_dir else "unknown"

    shots: list[dict] = []
    shot_index = 0

    for beat_idx, beat in enumerate(beats):
        role = beat.get("role", "")
        start_sec = beat.get("start_sec", 0.0)
        end_sec = beat.get("end_sec", 0.0)
        beat_duration = max(0.0, end_sec - start_sec)

        n_cuts = cuts_per_beat[beat_idx]

        # beat 내 각 컷의 duration = beat 길이 / 컷 수
        if n_cuts > 0 and beat_duration > 0:
            cut_duration = beat_duration / n_cuts
        elif n_cuts > 0:
            # duration 정보가 없으면 기본값
            cut_duration = 1.0
        else:
            continue

        # asset_type 결정: 모든 숏을 veo_i2v로 처리 (전체 동영상 생성)
        asset_type = "veo_i2v"

        # 프롬프트 구성
        prompt = _build_prompt_text(beat, profile, brief)

        for _ in range(n_cuts):
            # asset_path는 assets.py 단계에서 채워짐 — 여기서는 빈 문자열
            shot: dict = {
                "index": shot_index,
                "role": role,
                "asset_type": asset_type,
                "duration_sec": round(cut_duration, 4),
                "prompt": prompt,
                "asset_path": "",
            }
            shots.append(shot)
            shot_index += 1

    shotlist = {
        "run_id": run_id,
        "shots": shots,
    }

    logger.info(
        "[plan] 숏리스트 생성 완료: %d beats → %d shots (total_cuts=%d)",
        len(beats),
        len(shots),
        total_cuts,
    )
    return shotlist


def write_shotlist(shotlist: dict, run_dir: str) -> None:
    """숏리스트를 outputs/<run_id>/shotlist.json에 저장한다.

    run_dir 아래 shotlist.json으로 저장한다.
    write_json이 상위 디렉터리를 자동 생성하므로 별도 mkdir이 불필요하다.

    Args:
        shotlist: build_shotlist()가 반환한 숏리스트 dict
        run_dir: outputs/<run_id>/ 절대 경로

    Returns:
        None (요구사항 9.4, 16.4)
    """
    dest = Path(run_dir) / "shotlist.json"
    write_json(shotlist, dest)
    logger.info("[plan] shotlist.json 저장 완료: %s", dest)

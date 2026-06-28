"""
src/analyze/probe.py — ffprobe 기술 메타데이터 추출

레퍼런스 mp4에서 해상도·fps·duration·codec을 추출하고,
복수 레퍼런스를 style_profile의 format 섹션으로 변환한다.

요구사항: 2.1, 2.2, 2.3
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.common.exceptions import UnprocessableRefError


@dataclass
class ProbeResult:
    """ffprobe로 추출한 단일 영상 기술 메타데이터."""

    path: str
    width: int
    height: int
    fps: float
    duration_sec: float
    vcodec: str


def probe(path: str) -> ProbeResult:
    """ffprobe로 기술 메타데이터 추출.

    파일 부재 또는 비-mp4 확장자면 UnprocessableRefError를 raise한다.
    호출부는 이 예외를 잡아 해당 파일을 건너뛰고 계속 처리한다. (요구사항 2.1, 2.3)

    Args:
        path: 분석할 mp4 파일 경로.

    Returns:
        ProbeResult: 추출된 기술 메타데이터.

    Raises:
        UnprocessableRefError: 파일이 없거나, .mp4 확장자가 아니거나, ffprobe 실패,
                               또는 비디오 스트림이 없는 경우.
    """
    # 파일 존재 확인
    if not os.path.exists(path):
        raise UnprocessableRefError(
            f"레퍼런스 파일을 처리할 수 없습니다: {path}\n"
            f"  원인: 파일이 존재하지 않습니다.\n"
            f"  해결: 유효한 mp4 파일 경로를 --refs 인수로 전달하세요.",
            path=path,
        )

    # 확장자 확인 (대소문자 무관)
    ext = Path(path).suffix.lower()
    if ext != ".mp4":
        raise UnprocessableRefError(
            f"레퍼런스 파일을 처리할 수 없습니다: {path}\n"
            f"  원인: .mp4 형식이 아닙니다 (확장자: {ext!r}).\n"
            f"  해결: mp4 형식의 파일을 전달하세요.",
            path=path,
        )

    # ffprobe 실행
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise UnprocessableRefError(
            f"ffprobe를 실행할 수 없습니다. ffmpeg가 설치되어 있는지 확인하세요.\n"
            f"  설치: https://ffmpeg.org/download.html",
            path=path,
        )
    except subprocess.TimeoutExpired:
        raise UnprocessableRefError(
            f"ffprobe 실행이 타임아웃되었습니다: {path}",
            path=path,
        )

    if result.returncode != 0:
        raise UnprocessableRefError(
            f"ffprobe가 실패했습니다 (종료코드 {result.returncode}): {path}\n"
            f"  stderr: {result.stderr.strip()}",
            path=path,
        )

    # JSON 파싱
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise UnprocessableRefError(
            f"ffprobe 출력을 파싱할 수 없습니다: {path}\n"
            f"  원인: {e}",
            path=path,
        )

    # 비디오 스트림 탐색
    streams = data.get("streams", [])
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )

    if video_stream is None:
        raise UnprocessableRefError(
            f"비디오 스트림이 없습니다: {path}\n"
            f"  해결: 유효한 비디오 트랙이 포함된 mp4 파일을 전달하세요.",
            path=path,
        )

    # 너비·높이
    width: int = int(video_stream.get("width", 0))
    height: int = int(video_stream.get("height", 0))

    # fps 파싱 — r_frame_rate는 "30/1" 또는 "30000/1001" 형태
    r_frame_rate: str = video_stream.get("r_frame_rate", "0/1")
    fps = _parse_fraction(r_frame_rate)

    # duration_sec — format 섹션 우선, 없으면 스트림에서 시도
    fmt = data.get("format", {})
    duration_str = fmt.get("duration") or video_stream.get("duration", "0")
    try:
        duration_sec = float(duration_str)
    except (ValueError, TypeError):
        duration_sec = 0.0

    # codec 이름
    vcodec: str = video_stream.get("codec_name", "unknown")

    return ProbeResult(
        path=path,
        width=width,
        height=height,
        fps=fps,
        duration_sec=duration_sec,
        vcodec=vcodec,
    )


def to_format_section(results: list[ProbeResult]) -> dict:
    """ProbeResult 목록을 style_profile의 format 섹션으로 변환.

    aspect_ratio·resolution·fps·duration_sec_range를 산출한다. (요구사항 2.2)

    Args:
        results: probe()로 얻은 ProbeResult 목록. 비어 있으면 안 됨.

    Returns:
        dict: format 섹션 dict.
              {
                  "aspect_ratio": "9:16",
                  "resolution": "1080x1920",
                  "fps": 30.0,
                  "duration_sec_range": [10.0, 15.0],
              }
    """
    if not results:
        return {
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "fps": 30.0,
            "duration_sec_range": [0.0, 0.0],
        }

    # --- aspect_ratio: 가장 많이 등장하는 해상도의 비율 사용 ---
    resolution_counts: dict[tuple[int, int], int] = {}
    for r in results:
        key = (r.width, r.height)
        resolution_counts[key] = resolution_counts.get(key, 0) + 1

    # 최빈 해상도
    most_common_res = max(resolution_counts, key=lambda k: resolution_counts[k])
    w, h = most_common_res

    aspect_ratio = _simplify_aspect_ratio(w, h)
    resolution = f"{w}x{h}"

    # --- fps: 전체 평균 ---
    fps = sum(r.fps for r in results) / len(results)

    # --- duration_sec_range: [최솟값, 최댓값] ---
    durations = [r.duration_sec for r in results]
    duration_sec_range = [min(durations), max(durations)]

    return {
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "fps": round(fps, 3),
        "duration_sec_range": duration_sec_range,
    }


# ─── 내부 헬퍼 ───────────────────────────────────────────────────────────────


def _parse_fraction(fraction_str: str) -> float:
    """'30/1' 또는 '30000/1001' 형태의 문자열을 float으로 변환."""
    try:
        parts = fraction_str.split("/")
        if len(parts) == 2:
            num, den = float(parts[0]), float(parts[1])
            return num / den if den != 0 else 0.0
        return float(fraction_str)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _simplify_aspect_ratio(width: int, height: int) -> str:
    """너비·높이를 최대공약수로 약분해 'W:H' 문자열로 반환.

    일반적인 숏폼 비율(9:16)에 근사한 경우 '9:16'으로 정규화한다.
    math.gcd를 사용한다.
    """
    if width == 0 or height == 0:
        return "9:16"

    divisor = math.gcd(width, height)
    simplified_w = width // divisor
    simplified_h = height // divisor

    # 9:16에 충분히 가까운지 확인 (허용오차 1%)
    target_ratio = 9 / 16
    actual_ratio = width / height
    if abs(actual_ratio - target_ratio) / target_ratio < 0.01:
        return "9:16"

    return f"{simplified_w}:{simplified_h}"

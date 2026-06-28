"""
src/analyze/cut_detect.py — ffmpeg 씬 감지 및 페이싱 계산

요구사항: 3.1, 3.2, 3.3, 3.4, 3.5
"""

from __future__ import annotations

import logging
import re
import subprocess
from statistics import mode as statistics_mode

logger = logging.getLogger(__name__)

# hook_cut_density 임계값: 첫 3초 내 컷 수 기준
_HOOK_WINDOW_SEC = 3.0
_HOOK_HIGH_THRESHOLD = 3   # ≥ 3 → "high"
_HOOK_MEDIUM_MIN = 1       # 1~2 → "medium"

# rhythm_mode 임계값: avg_shot_len_sec 기준
_FAST_MONTAGE_MAX = 1.5    # < 1.5 → fast_montage
_SLOW_HOLD_MIN = 3.0       # > 3.0 → slow_hold


def detect_cuts(path: str, *, scene_threshold: float = 0.3) -> list[float]:
    """ffmpeg 씬 감지로 컷 타임스탬프(초) 정렬 리스트를 반환한다. (요구사항 3.1)

    항상 0.0을 첫 번째 컷으로 포함한다. 씬 감지 결과가 없어 [0.0]만 남을 경우
    단일 숏으로 처리하고 경고를 기록한다. (요구사항 3.5)

    Args:
        path: 분석할 mp4 파일 경로.
        scene_threshold: ffmpeg select 필터의 씬 전환 임계값 (0~1, 기본 0.3).

    Returns:
        정렬된 컷 타임스탬프(초) 리스트. 최소 [0.0].
    """
    cmd = [
        "ffmpeg",
        "-i", path,
        "-vf", f"select=gt(scene\\,{scene_threshold}),showinfo",
        "-f", "null",
        "-",
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output = result.stdout
    except FileNotFoundError:
        logger.warning(
            "ffmpeg 실행 파일을 찾을 수 없습니다. PATH를 확인하세요. "
            "경로 %s 를 단일 숏으로 처리합니다.",
            path,
        )
        return [0.0]

    # pts_time:<timestamp> 패턴 파싱
    timestamps: list[float] = [0.0]
    for match in re.finditer(r"pts_time:([\d.]+)", output):
        try:
            ts = float(match.group(1))
            if ts > 0.0:
                timestamps.append(ts)
        except ValueError:
            continue

    # 중복 제거 및 정렬
    timestamps = sorted(set(timestamps))

    if len(timestamps) == 1:
        logger.warning(
            "No cuts detected in %s, treating as single shot", path
        )

    return timestamps


def compute_pacing_metrics(cuts: list[float], duration: float) -> dict:
    """단일 영상의 페이싱 지표를 계산한다. (요구사항 3.2, 3.4)

    Args:
        cuts: 정렬된 컷 타임스탬프(초) 리스트. 최소 [0.0].
        duration: 영상 총 재생 시간(초).

    Returns:
        다음 키를 포함하는 dict:
            cut_count (int): 숏 개수
            avg_shot_len_sec (float): 평균 숏 길이(초)
            shot_len_distribution_sec (list[float]): 각 숏 길이(초) 목록
            hook_cut_density (str): "high" | "medium" | "low"
    """
    # cut_count: 씬 전환 컷 횟수 (숏 개수와는 다름; 구간 사이 전환 수)
    # 설계 명세: len(cuts) - 1 (컷 이벤트 수). cuts=[0.0]이면 전환 없음 → 1
    if len(cuts) > 1:
        cut_count = len(cuts) - 1
    else:
        cut_count = 1

    # avg_shot_len_sec: duration / cut_count
    avg_shot_len_sec = duration / cut_count if cut_count > 0 else duration

    # shot_len_distribution_sec: 각 숏(구간) 길이
    # cuts=[0.0, 5.0], duration=10 → shots: [0→5]=5.0, [5→10]=5.0 (2 shots)
    # 총 숏 수 = len(cuts) (각 컷 시작점 + 마지막 구간)
    if len(cuts) > 1:
        shot_lengths = [
            max(0.0, cuts[i + 1] - cuts[i])
            for i in range(len(cuts) - 1)
        ]
        # 마지막 컷부터 끝까지
        last_shot = max(0.0, duration - cuts[-1])
        shot_lengths.append(last_shot)
    else:
        shot_lengths = [max(0.0, duration)]

    # hook_cut_density: 첫 3초 구간의 컷 수 기준 (요구사항 3.4)
    # cuts[0] = 0.0은 첫 시작점이므로 인덱스 1부터 집계
    hook_cuts = sum(1 for t in cuts[1:] if t < _HOOK_WINDOW_SEC)
    if hook_cuts >= _HOOK_HIGH_THRESHOLD:
        hook_cut_density = "high"
    elif hook_cuts >= _HOOK_MEDIUM_MIN:
        hook_cut_density = "medium"
    else:
        hook_cut_density = "low"

    return {
        "cut_count": cut_count,
        "avg_shot_len_sec": avg_shot_len_sec,
        "shot_len_distribution_sec": shot_lengths,
        "hook_cut_density": hook_cut_density,
    }


def merge_pacing(per_ref: list[dict]) -> dict:
    """복수 레퍼런스의 페이싱 지표를 병합한다. (요구사항 3.3)

    Args:
        per_ref: 각 레퍼런스의 compute_pacing_metrics 반환값 리스트.

    Returns:
        다음 키를 포함하는 dict:
            cut_count_range (list[int]): [min, max] 컷 수 범위
            avg_shot_len_sec (float): 전체 평균 숏 길이(초)
            shot_len_distribution_sec (list[float]): 모든 숏 길이 통합·정렬 목록
            rhythm_mode (str): "fast_montage" | "slow_hold" | "mixed"
            hook_cut_density (str): 최빈값 ("high" > "medium" > "low" 우선)
    """
    if not per_ref:
        return {
            "cut_count_range": [0, 0],
            "avg_shot_len_sec": 0.0,
            "shot_len_distribution_sec": [],
            "rhythm_mode": "mixed",
            "hook_cut_density": "low",
        }

    cut_counts: list[int] = [m["cut_count"] for m in per_ref]
    avg_shot_lens: list[float] = [m["avg_shot_len_sec"] for m in per_ref]
    all_shot_lengths: list[float] = []
    for m in per_ref:
        all_shot_lengths.extend(m["shot_len_distribution_sec"])
    hook_densities: list[str] = [m["hook_cut_density"] for m in per_ref]

    # cut_count_range
    cut_count_range = [min(cut_counts), max(cut_counts)]

    # avg_shot_len_sec: 전체 평균
    overall_avg = sum(avg_shot_lens) / len(avg_shot_lens)

    # shot_len_distribution_sec: 통합 정렬
    all_shot_lengths_sorted = sorted(all_shot_lengths)

    # rhythm_mode: avg_shot_len_sec 기준
    if overall_avg < _FAST_MONTAGE_MAX:
        rhythm_mode = "fast_montage"
    elif overall_avg > _SLOW_HOLD_MIN:
        rhythm_mode = "slow_hold"
    else:
        rhythm_mode = "mixed"

    # hook_cut_density: 최빈값, 동률 시 "high" > "medium" > "low" 우선
    density_priority = {"high": 0, "medium": 1, "low": 2}
    density_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for d in hook_densities:
        density_counts[d] = density_counts.get(d, 0) + 1

    max_count = max(density_counts.values())
    # 최빈값 중 우선순위가 높은(숫자 낮은) 것 선택
    merged_hook_density = min(
        (k for k, v in density_counts.items() if v == max_count),
        key=lambda k: density_priority[k],
    )

    return {
        "cut_count_range": cut_count_range,
        "avg_shot_len_sec": overall_avg,
        "shot_len_distribution_sec": all_shot_lengths_sorted,
        "rhythm_mode": rhythm_mode,
        "hook_cut_density": merged_hook_density,
    }

"""
src/generate/plan.py — 숏리스트 플래닝 (요구사항 9.1~9.4, 16.4)

beat_sheet를 순회해 각 장면의 숏 계획을 수립하고 shotlist.json으로 저장한다.

핵심 설계 원칙:
- narrative.beats가 스토리 구조의 유일한 출처 (1 beat = 1 shot)
- pacing.cut_count_range는 분석 단계의 메타데이터로만 사용 (생성에는 미사용)
- role이 hook·application이면 'veo_i2v', 나머지(product_hero·result_glow 등)는
  'imagen_image'로 처리 — Veo 호출 수를 role 기준으로 제한해 quota 소진 시
  영향 범위를 줄인다.
- src/analyze/ 를 절대 import하지 않는다 (요구사항 13.2)
- 벤더 호출 없음 — 이 모듈은 순수 로직 레이어

재생산성 보장:
- beats 수 = 생성될 shots 수 (명확한 1:1 대응)
- cut_count_range를 샘플링하지 않음 → 매번 동일한 구조 재현
"""

from __future__ import annotations

import copy
import logging
import random
from pathlib import Path

from src.common.io import write_json

logger = logging.getLogger(__name__)

# 과제 스펙상 최종 영상은 "숏폼" 범주(15~60초)여야 한다. 분석된 레퍼런스의
# 원본 재생 시간(format.duration_sec_range)은 이 범위 밖일 수 있으므로(예: 10.7초),
# 생성 단계에서 비율을 유지한 채 이 범위 안으로 정규화한다.
_MIN_SHORTFORM_SEC = 15.0
_MAX_SHORTFORM_SEC = 60.0

# rhythm_mode 재라벨링 임계값. src/analyze/cut_detect.py의 값과 의도적으로 동일하게
# 맞췄지만, plan.py는 src/analyze/를 import하지 않으므로(요구사항 13.2) 상수를 복제한다.
_FAST_MONTAGE_MAX = 1.5
_SLOW_HOLD_MIN = 3.0

# Veo(veo_i2v)로 처리할 role. 나머지 role은 imagen_image(정지 이미지)로 처리한다.
# hook·application은 움직임이 시청 경험에 가장 크게 기여하는 구간이라 우선 배정하고,
# product_hero·result_glow 등은 정지 이미지로도 정보 전달에 무리가 없어 Veo 호출
# 수를 줄이는 데 우선적으로 뺀다 (Veo quota 소진 시 영향 범위 축소).
_VEO_ROLES = {"hook", "application"}


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

# NOTE: _distribute_cuts() 함수는 더 이상 사용하지 않음.
# 새로운 설계에서는 1 beat = 1 shot 고정 원칙을 따름.


def _build_prompt_text(beat: dict, profile: dict, brief: dict) -> str:
    """단일 숏(shot)에 대한 Imagen 생성 프롬프트를 구성한다.

    형식:
    "9:16 vertical short-form beauty video frame: {shot_type}, {intent}.
     Style: {color_grade}, {lighting}. Setting: {setting}. Subject: {product_subject}."

    setting(visual.setting, 예: "home_interior_daylight_plant_background")이
    분석 단계에서 추출되지만 기존에는 프롬프트에 반영되지 않았다 — "권장 - 배경"
    항목을 충족하기 위해 Setting 절을 별도로 추가한다. Style(색감·조명)과는
    다른 축(장면의 물리적 환경)이라 별도 문구로 분리했다.

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
    setting = visual.get("setting", "")

    user_input = brief.get("user_input", {})
    product_subject = user_input.get("value", "beauty product")
    if user_input.get("kind") in ("image", "video"):
        product_subject = Path(product_subject).stem if product_subject else "beauty product"

    # 악센트 색상이 있으면 스타일에 포함
    style_parts = [color_grade, lighting]
    if accent_color:
        style_parts.append(f"accent {accent_color}")
    style_str = ", ".join(style_parts)

    # setting은 snake_case로 저장되어 있으므로 프롬프트에서는 읽기 쉽게 공백으로 치환
    setting_str = setting.replace("_", " ") if setting else ""

    prompt = (
        f"9:16 vertical short-form beauty video frame: {shot_type}, {intent}. "
        f"Style: {style_str}. "
    )
    if setting_str:
        prompt += f"Setting: {setting_str}. "
    prompt += f"Subject: {product_subject}."
    return prompt


# ---------------------------------------------------------------------------
# 퍼블릭 API
# ---------------------------------------------------------------------------

def normalize_profile_duration(
    profile: dict,
    *,
    target_sec: float | None = None,
    enforce_min: bool = True,
) -> dict:
    """profile의 재생 시간을 숏폼 허용 범위([15, 60]초)로 정규화한다.

    분석 단계는 레퍼런스 mp4의 실제 재생 시간을 그대로 format.duration_sec_range에
    담는다 — 이 값이 레퍼런스마다 다르고(예: 8초, 10.7초, 45초 등) 과제가 요구하는
    숏폼 범위(15~60초) 밖일 수 있다. 이 함수는 narrative.beats·captions.slots·
    format.duration_sec_range·pacing(avg_shot_len_sec, shot_len_distribution_sec,
    rhythm_mode)을 비율을 유지한 채 스케일링해 항상 유효한 재생 시간을 만든다.
    cut_count_range(컷 수)는 바꾸지 않는다 — 레퍼런스의 컷 리듬 자체는 보존한다.

    15초 하한은 "숏폼 범주" 권장값이며 필수 제약은 아니다. target_sec을 음악
    실제 길이 등 명시적 근거로 지정하는 경우 enforce_min=False로 하한 클램프를
    건너뛸 수 있다 (60초 상한은 항상 적용 — 이쪽은 안전장치).

    Args:
        profile: style_profile dict.
        target_sec: 명시하면 이 값(초)에 강제로 맞춘다(허용 범위로 클램프됨).
            None이면 원본 재생 시간이 이미 허용 범위 안이면 그대로 두고,
            범위 밖이면 가까운 경계값(15 또는 60)으로 클램프한다.
        enforce_min: False이면 15초 하한 클램프를 건너뛴다 (기본값: True).
            target_sec이 음악 길이처럼 근거 있는 값일 때 사용한다.

    Returns:
        정규화된 새 profile dict (원본은 변경하지 않음).
    """
    normalized = copy.deepcopy(profile)

    fmt = normalized.get("format", {})
    duration_range = fmt.get("duration_sec_range", [0.0, 0.0])
    raw = float(duration_range[-1]) if duration_range else 0.0
    if raw <= 0:
        logger.warning("[plan] duration_sec_range가 비어있어 정규화를 건너뜁니다.")
        return normalized

    min_bound = _MIN_SHORTFORM_SEC if enforce_min else 0.0

    if target_sec is not None:
        desired = min(max(target_sec, min_bound), _MAX_SHORTFORM_SEC)
    elif min_bound <= raw <= _MAX_SHORTFORM_SEC:
        return normalized  # 이미 허용 범위 안 — 레퍼런스의 원래 길이를 존중
    else:
        desired = min(max(raw, min_bound), _MAX_SHORTFORM_SEC)

    scale = desired / raw
    if abs(scale - 1.0) < 1e-9:
        return normalized

    fmt["duration_sec_range"] = [round(v * scale, 4) for v in duration_range]

    for beat in normalized.get("narrative", {}).get("beats", []):
        beat["start_sec"] = round(beat.get("start_sec", 0.0) * scale, 4)
        beat["end_sec"] = round(beat.get("end_sec", 0.0) * scale, 4)

    for slot in normalized.get("captions", {}).get("slots", []):
        slot["appear_sec"] = round(slot.get("appear_sec", 0.0) * scale, 4)
        slot["duration_sec"] = round(slot.get("duration_sec", 0.0) * scale, 4)

    pacing = normalized.get("pacing", {})
    if "avg_shot_len_sec" in pacing:
        pacing["avg_shot_len_sec"] = round(pacing["avg_shot_len_sec"] * scale, 4)
    if "shot_len_distribution_sec" in pacing:
        pacing["shot_len_distribution_sec"] = [
            round(v * scale, 4) for v in pacing["shot_len_distribution_sec"]
        ]
    avg_shot_len = pacing.get("avg_shot_len_sec")
    if avg_shot_len is not None:
        if avg_shot_len < _FAST_MONTAGE_MAX:
            pacing["rhythm_mode"] = "fast_montage"
        elif avg_shot_len > _SLOW_HOLD_MIN:
            pacing["rhythm_mode"] = "slow_hold"
        else:
            pacing["rhythm_mode"] = "mixed"

    logger.info(
        "[plan] duration 정규화: %.2fs → %.2fs (scale=%.4f)", raw, desired, scale
    )
    return normalized


def build_shotlist(
    brief: dict,
    profile: dict,
    hook_text: str,
    *,
    rng: random.Random,
) -> dict:
    """narrative.beats를 1:1로 shots로 변환한다.

    새로운 설계 원칙:
    - 1 beat = 1 shot (명확한 대응)
    - pacing.cut_count_range는 무시 (분석 메타데이터로만 유지)
    - role이 hook·application이면 'veo_i2v', 나머지는 'imagen_image'
    - beat의 duration을 그대로 shot의 duration으로 사용

    재생산성 보장:
    - beats가 유일한 출처 → 매번 동일한 구조
    - rng 샘플링 없음 → 비결정적 요소 제거

    Args:
        brief: build_brief()가 반환한 브리프 dict
        profile: style_profile dict
        hook_text: generate_hook()이 반환한 훅 텍스트 (프롬프트에 포함 가능)
        rng: random.Random 인스턴스 (하위 호환성 유지, 현재는 미사용)

    Returns:
        shotlist dict (run_id, shots 배열)
    """
    beats = profile.get("narrative", {}).get("beats", [])

    if not beats:
        logger.warning("[plan] narrative.beats가 비어 있습니다. 빈 숏리스트를 반환합니다.")
        run_id = Path(brief.get("run_dir", "outputs/unknown")).name if brief.get("run_dir") else "unknown"
        return {"run_id": run_id, "shots": []}

    # run_id 추출
    run_dir = brief.get("run_dir", "")
    run_id = Path(run_dir).name if run_dir else "unknown"

    shots: list[dict] = []

    for shot_index, beat in enumerate(beats):
        role = beat.get("role", "")
        start_sec = beat.get("start_sec", 0.0)
        end_sec = beat.get("end_sec", 0.0)
        beat_duration = max(0.0, end_sec - start_sec)

        # duration이 0이면 기본값 1초
        shot_duration = beat_duration if beat_duration > 0 else 1.0

        # hook·application만 veo_i2v, 나머지는 imagen_image
        asset_type = "veo_i2v" if role in _VEO_ROLES else "imagen_image"

        # 프롬프트 구성
        prompt = _build_prompt_text(beat, profile, brief)

        shot: dict = {
            "index": shot_index,
            "role": role,
            "asset_type": asset_type,
            "duration_sec": round(shot_duration, 4),
            "prompt": prompt,
            "asset_path": "",  # assets.py 단계에서 채워짐
        }
        shots.append(shot)

    shotlist = {
        "run_id": run_id,
        "shots": shots,
    }

    logger.info(
        "[plan] 숏리스트 생성 완료: %d beats → %d shots (1:1 매칭)",
        len(beats),
        len(shots),
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

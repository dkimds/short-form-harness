"""
src/analyze/synthesize_profile.py — 분석 결과 통합, 스키마 검증, 저장

모든 분석 단계 결과(probe, cut_detect, audio_stats, vision)를
하나의 style_profile dict로 통합하고, jsonschema로 검증한 뒤
검증을 통과한 경우에만 파일로 저장한다.

요구사항: 6.1, 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import jsonschema

from src.common.exceptions import ProfileValidationError

# 스키마 파일 경로: 레포 루트 / style_profile.schema.json
_SCHEMA_PATH = Path(__file__).parent.parent.parent / "style_profile.schema.json"

# 장르 기본값
_DEFAULT_GENRE = "beauty_skincare_ugc_ad"


def synthesize(
    probe_data: dict,
    pacing: dict,
    audio: dict,
    vision: dict,
    *,
    source_refs: list[str],
    extracted_by: str,
) -> dict:
    """모든 분석 결과를 style_profile dict로 통합한다.

    meta.profile_id를 자동 생성하고, meta.extracted_by를 기록한다. (요구사항 6.4)
    레퍼런스 경로·프레임 데이터는 meta.source_refs(추적용 문자열)를 제외하고
    포함하지 않는다. (요구사항 6.5)

    Args:
        probe_data: to_format_section()이 반환한 format 섹션 dict.
        pacing: merge_pacing()이 반환한 pacing 섹션 dict.
        audio: AudioStats를 dict로 변환한 값
               (music_start_sec, target_lufs, has_voiceover 키 포함).
        vision: analyze_vision()이 반환한 dict
                (narrative, captions, visual, audio 서브딕트 포함 가능).
        source_refs: 추적용 레퍼런스 파일 경로 문자열 목록.
        extracted_by: 분석에 사용한 모델명/버전 (예: "gemini-2.0-flash").

    Returns:
        style_profile.schema.json에 부합하는 profile dict.
    """
    # --- meta ---
    profile_id = uuid.uuid4().hex[:12]
    genre = vision.get("genre", _DEFAULT_GENRE) if vision else _DEFAULT_GENRE

    meta = {
        "profile_id": profile_id,
        "source_refs": list(source_refs),
        "extracted_by": extracted_by,
        "genre": genre,
    }

    # --- format ---
    # probe_data는 to_format_section() 반환값
    format_section = {
        "aspect_ratio": probe_data.get("aspect_ratio", "9:16"),
        "resolution": probe_data.get("resolution", "576x1024"),
        "fps": probe_data.get("fps", 30),
        "duration_sec_range": probe_data.get("duration_sec_range", [10, 15]),
    }

    # --- pacing ---
    pacing_section = {
        "cut_count_range": pacing.get("cut_count_range", [1, 1]),
        "avg_shot_len_sec": pacing.get("avg_shot_len_sec", 0.0),
        "shot_len_distribution_sec": pacing.get("shot_len_distribution_sec", []),
        "rhythm_mode": pacing.get("rhythm_mode", "mixed"),
        "hook_cut_density": pacing.get("hook_cut_density", "low"),
    }

    # --- audio ---
    # audio_stats 필드 + vision audio 필드 병합
    vision_audio = (vision.get("audio", {}) if vision else {}) or {}

    audio_section: dict = {
        "music_start_sec": audio.get("music_start_sec", 0.0),
        "target_lufs": audio.get("target_lufs", -23.0),
        "has_voiceover": audio.get("has_voiceover", False),
    }

    # vision에서 추가 audio 필드 (있으면 병합)
    if "music_mood" in vision_audio:
        audio_section["music_mood"] = vision_audio["music_mood"]
    if "vo_style" in vision_audio:
        audio_section["vo_style"] = vision_audio["vo_style"]

    # --- narrative ---
    narrative_section = (
        vision.get("narrative", {"beats": []}) if vision else {"beats": []}
    )
    if narrative_section is None:
        narrative_section = {"beats": []}

    # --- captions ---
    captions_section = (
        vision.get("captions", {"slots": []}) if vision else {"slots": []}
    )
    if captions_section is None:
        captions_section = {"slots": []}

    # --- overlay ---
    _default_overlay = {
        "platform_watermark": "placeholder",
        "handle_position": "left_mid",
        "end_card": "none",
    }
    overlay_section = vision.get("overlay", _default_overlay) if vision else _default_overlay
    if overlay_section is None:
        overlay_section = _default_overlay

    # --- visual ---
    visual_section = vision.get("visual", {}) if vision else {}
    if visual_section is None:
        visual_section = {}

    profile = {
        "meta": meta,
        "format": format_section,
        "pacing": pacing_section,
        "captions": captions_section,
        "audio": audio_section,
        "overlay": overlay_section,
        "narrative": narrative_section,
        "visual": visual_section,
    }

    return profile


def validate_profile(profile: dict) -> list[str]:
    """style_profile.schema.json으로 profile을 검증한다. (요구사항 6.1)

    jsonschema Draft7Validator를 사용해 모든 위반을 수집하고
    사람이 읽을 수 있는 문자열 목록으로 반환한다.

    Args:
        profile: 검증할 profile dict.

    Returns:
        위반 항목 문자열 목록. 빈 리스트이면 통과.
    """
    with _SCHEMA_PATH.open(encoding="utf-8") as f:
        schema = json.load(f)

    validator = jsonschema.Draft7Validator(schema)
    violations: list[str] = []

    for error in validator.iter_errors(profile):
        # 경로가 있으면 "$." + ".".join(path), 없으면 "$" 사용
        if error.absolute_path:
            field_path = "$." + ".".join(str(p) for p in error.absolute_path)
        else:
            field_path = "$"
        violations.append(f"{field_path}: {error.message}")

    return violations


def save_profile(profile: dict, out_path: str) -> None:
    """검증을 통과한 경우에만 profile을 파일로 저장한다. (요구사항 6.2, 6.3)

    검증 실패 시 각 위반 항목을 출력하고 ProfileValidationError를 raise한다.
    파일은 저장되지 않는다.

    Args:
        profile: 저장할 profile dict.
        out_path: 저장할 파일 경로 (문자열).

    Raises:
        ProfileValidationError: 스키마 검증 실패 시.
    """
    violations = validate_profile(profile)

    if violations:
        print("프로파일 스키마 검증 실패. 위반 항목:")
        for v in violations:
            print(f"  - {v}")
        raise ProfileValidationError(
            f"프로파일이 스키마 검증을 통과하지 못했습니다.\n"
            f"  위반 항목: {violations}\n"
            f"  스키마 파일: style_profile.schema.json\n"
            f"  해결: 위반된 필드를 수정하거나 analyze 단계를 다시 실행하세요.",
            violations=violations,
        )

    # 검증 통과 → 파일 저장
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")

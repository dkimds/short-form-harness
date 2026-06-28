"""
tests/test_synthesize_profile.py — synthesize_profile 단위 테스트

요구사항: 6.1, 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.analyze.synthesize_profile import (
    save_profile,
    synthesize,
    validate_profile,
)
from src.common.exceptions import ProfileValidationError

# ─── 테스트용 최소 유효 입력 픽스처 ──────────────────────────────────────────


@pytest.fixture()
def probe_data() -> dict:
    return {
        "aspect_ratio": "9:16",
        "resolution": "576x1024",
        "fps": 30,
        "duration_sec_range": [10.0, 15.0],
    }


@pytest.fixture()
def pacing_data() -> dict:
    return {
        "cut_count_range": [4, 12],
        "avg_shot_len_sec": 1.5,
        "shot_len_distribution_sec": [0.8, 1.2, 1.5, 2.0],
        "rhythm_mode": "fast_montage",
        "hook_cut_density": "high",
    }


@pytest.fixture()
def audio_data() -> dict:
    return {
        "music_start_sec": 0.0,
        "target_lufs": -23.0,
        "has_voiceover": False,
    }


@pytest.fixture()
def vision_data() -> dict:
    return {
        "narrative": {
            "beats": [
                {
                    "role": "hook",
                    "start_sec": 0.0,
                    "end_sec": 2.5,
                    "shot_type": "extreme_closeup_handheld_selfie",
                    "intent": "grab attention",
                }
            ]
        },
        "captions": {
            "slots": [
                {
                    "name": "title_hook",
                    "anchor": "top_center",
                    "font_style": "white_semibold_soft_shadow",
                    "size_pct": 5.0,
                    "appear_sec": 0.0,
                    "duration_sec": 2.5,
                    "emoji_palette": ["sparkle"],
                    "is_hook": True,
                }
            ]
        },
        "visual": {
            "color_grade": "warm_soft_pastel",
            "lighting": "natural_window_soft",
            "accent_color": "#E8A0B8",
            "creator_count": 1,
            "setting": "home_interior_daylight",
        },
        "audio": {
            "music_mood": "soft_upbeat_aesthetic",
            "vo_style": "whisper_asmr",
        },
        "overlay": {
            "platform_watermark": "tiktok_logo_plus_handle",
            "handle_position": "left_mid",
            "end_card": "none",
        },
    }


@pytest.fixture()
def valid_profile(probe_data, pacing_data, audio_data, vision_data) -> dict:
    return synthesize(
        probe_data,
        pacing_data,
        audio_data,
        vision_data,
        source_refs=["refs/reference1.mp4", "refs/reference2.mp4"],
        extracted_by="gemini-2.0-flash",
    )


# ─── synthesize() 테스트 ──────────────────────────────────────────────────────


class TestSynthesize:
    def test_returns_dict_with_all_required_top_level_keys(
        self, probe_data, pacing_data, audio_data, vision_data
    ):
        """synthesize()는 8개 필수 최상위 키를 가진 dict를 반환해야 한다."""
        profile = synthesize(
            probe_data,
            pacing_data,
            audio_data,
            vision_data,
            source_refs=["refs/ref.mp4"],
            extracted_by="gemini-2.0-flash",
        )

        required_keys = {"meta", "format", "pacing", "captions", "audio", "overlay", "narrative", "visual"}
        assert required_keys == set(profile.keys()), (
            f"누락된 키: {required_keys - set(profile.keys())}"
        )

    def test_meta_profile_id_is_non_empty_string(
        self, probe_data, pacing_data, audio_data, vision_data
    ):
        """meta.profile_id는 비어 있지 않은 문자열이어야 한다. (요구사항 6.4)"""
        profile = synthesize(
            probe_data,
            pacing_data,
            audio_data,
            vision_data,
            source_refs=[],
            extracted_by="gemini-2.0-flash",
        )

        profile_id = profile["meta"]["profile_id"]
        assert isinstance(profile_id, str), "profile_id는 문자열이어야 한다"
        assert len(profile_id) > 0, "profile_id는 비어 있으면 안 된다"

    def test_meta_profile_id_is_different_each_call(
        self, probe_data, pacing_data, audio_data, vision_data
    ):
        """매 호출마다 서로 다른 profile_id가 생성되어야 한다."""
        profile1 = synthesize(
            probe_data, pacing_data, audio_data, vision_data,
            source_refs=[], extracted_by="gemini-2.0-flash",
        )
        profile2 = synthesize(
            probe_data, pacing_data, audio_data, vision_data,
            source_refs=[], extracted_by="gemini-2.0-flash",
        )
        assert profile1["meta"]["profile_id"] != profile2["meta"]["profile_id"]

    def test_meta_source_refs_contains_passed_source_refs(
        self, probe_data, pacing_data, audio_data, vision_data
    ):
        """meta.source_refs에 전달된 source_refs가 포함되어야 한다. (요구사항 6.5)"""
        refs = ["refs/reference1.mp4", "refs/reference2.mp4"]
        profile = synthesize(
            probe_data,
            pacing_data,
            audio_data,
            vision_data,
            source_refs=refs,
            extracted_by="gemini-2.0-flash",
        )

        assert profile["meta"]["source_refs"] == refs

    def test_meta_extracted_by_is_recorded(
        self, probe_data, pacing_data, audio_data, vision_data
    ):
        """meta.extracted_by에 전달한 모델명이 기록되어야 한다. (요구사항 6.4)"""
        profile = synthesize(
            probe_data,
            pacing_data,
            audio_data,
            vision_data,
            source_refs=[],
            extracted_by="gemini-2.0-flash",
        )
        assert profile["meta"]["extracted_by"] == "gemini-2.0-flash"

    def test_audio_merges_audio_stats_and_vision_audio(
        self, probe_data, pacing_data, audio_data, vision_data
    ):
        """audio 섹션은 AudioStats 필드와 vision의 audio 필드를 병합해야 한다."""
        profile = synthesize(
            probe_data,
            pacing_data,
            audio_data,
            vision_data,
            source_refs=[],
            extracted_by="gemini-2.0-flash",
        )

        audio_section = profile["audio"]
        # AudioStats에서 온 필드
        assert audio_section["music_start_sec"] == 0.0
        assert audio_section["target_lufs"] == -23.0
        assert audio_section["has_voiceover"] is False
        # vision audio에서 온 필드
        assert audio_section["music_mood"] == "soft_upbeat_aesthetic"
        assert audio_section["vo_style"] == "whisper_asmr"

    def test_no_raw_file_paths_outside_source_refs(
        self, probe_data, pacing_data, audio_data, vision_data
    ):
        """meta.source_refs를 제외한 어떤 필드에도 .mp4 경로가 포함되면 안 된다. (요구사항 6.5)"""
        refs = ["refs/reference1.mp4", "refs/reference2.mp4"]
        profile = synthesize(
            probe_data,
            pacing_data,
            audio_data,
            vision_data,
            source_refs=refs,
            extracted_by="gemini-2.0-flash",
        )

        # source_refs를 제거한 뒤 직렬화해서 .mp4가 나오면 안 된다
        profile_without_source_refs = json.loads(json.dumps(profile))
        profile_without_source_refs["meta"].pop("source_refs", None)
        serialized = json.dumps(profile_without_source_refs)
        assert ".mp4" not in serialized, (
            "source_refs 외 필드에 .mp4 경로가 포함되어 있다"
        )

    def test_empty_vision_uses_defaults(self, probe_data, pacing_data, audio_data):
        """vision이 빈 dict일 때 기본값이 사용되어야 한다."""
        profile = synthesize(
            probe_data,
            pacing_data,
            audio_data,
            {},
            source_refs=[],
            extracted_by="test-model",
        )
        assert "narrative" in profile
        assert "captions" in profile
        assert "overlay" in profile
        assert "visual" in profile


# ─── validate_profile() 테스트 ────────────────────────────────────────────────


class TestValidateProfile:
    def test_returns_empty_list_for_valid_profile(self, valid_profile):
        """유효한 profile에 대해 빈 리스트를 반환해야 한다. (요구사항 6.1)"""
        violations = validate_profile(valid_profile)
        assert violations == [], f"예상치 못한 위반 항목: {violations}"

    def test_returns_non_empty_list_for_missing_required_key(self, valid_profile):
        """필수 키가 없는 경우 비어 있지 않은 위반 목록을 반환해야 한다. (요구사항 6.1)"""
        invalid = dict(valid_profile)
        del invalid["format"]  # 필수 키 제거

        violations = validate_profile(invalid)
        assert len(violations) > 0, "위반이 감지되지 않았다"

    def test_returns_non_empty_list_for_wrong_type(self, valid_profile):
        """잘못된 타입의 필드에 대해 위반 목록을 반환해야 한다."""
        import copy
        invalid = copy.deepcopy(valid_profile)
        # fps는 integer여야 하는데 문자열로 설정
        invalid["format"]["fps"] = "not_a_number"

        violations = validate_profile(invalid)
        assert len(violations) > 0

    def test_violations_contain_field_path(self, valid_profile):
        """위반 항목 문자열에 필드 경로 정보가 포함되어야 한다."""
        invalid = dict(valid_profile)
        del invalid["pacing"]  # 필수 키 제거

        violations = validate_profile(invalid)
        assert len(violations) > 0
        # 위반 문자열이 '$'로 시작하는지 확인
        assert any(v.startswith("$") for v in violations)

    def test_empty_dict_returns_violations(self):
        """빈 dict는 모든 필수 키 위반을 반환해야 한다."""
        violations = validate_profile({})
        assert len(violations) > 0


# ─── save_profile() 테스트 ────────────────────────────────────────────────────


class TestSaveProfile:
    def test_writes_file_when_valid(self, valid_profile):
        """유효한 profile은 파일로 저장되어야 한다. (요구사항 6.2)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_profile.json")
            save_profile(valid_profile, out_path)

            assert os.path.exists(out_path), "파일이 생성되지 않았다"

            with open(out_path, encoding="utf-8") as f:
                saved = json.load(f)

            assert saved["meta"]["profile_id"] == valid_profile["meta"]["profile_id"]

    def test_saved_file_is_valid_json(self, valid_profile):
        """저장된 파일은 유효한 JSON이어야 한다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_profile.json")
            save_profile(valid_profile, out_path)

            content = Path(out_path).read_text(encoding="utf-8")
            parsed = json.loads(content)
            assert isinstance(parsed, dict)

    def test_creates_parent_directories(self, valid_profile):
        """저장 경로의 상위 디렉터리가 없을 때 자동 생성해야 한다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "subdir1", "subdir2", "profile.json")
            save_profile(valid_profile, out_path)

            assert os.path.exists(out_path)

    def test_raises_profile_validation_error_when_invalid(self, valid_profile):
        """유효하지 않은 profile에서 ProfileValidationError를 raise해야 한다. (요구사항 6.3)"""
        invalid = dict(valid_profile)
        del invalid["audio"]  # 필수 키 제거

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "should_not_be_created.json")

            with pytest.raises(ProfileValidationError):
                save_profile(invalid, out_path)

    def test_does_not_write_file_when_invalid(self, valid_profile):
        """유효하지 않은 profile은 파일을 저장하지 않아야 한다. (요구사항 6.3)"""
        invalid = dict(valid_profile)
        del invalid["visual"]  # 필수 키 제거

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "should_not_exist.json")

            with pytest.raises(ProfileValidationError):
                save_profile(invalid, out_path)

            assert not os.path.exists(out_path), (
                "검증 실패 시 파일이 저장되면 안 된다"
            )

    def test_profile_validation_error_has_violations_list(self, valid_profile):
        """ProfileValidationError에 violations 목록이 포함되어야 한다."""
        invalid = dict(valid_profile)
        del invalid["narrative"]

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "fail.json")

            with pytest.raises(ProfileValidationError) as exc_info:
                save_profile(invalid, out_path)

            assert len(exc_info.value.violations) > 0

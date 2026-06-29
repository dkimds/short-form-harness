"""
tests/test_hook_gen.py — hook_gen 단위 테스트

generate_hook 및 fill_hook_slot 함수를 검증한다.
VendorClient는 모킹해 외부 API를 호출하지 않는다.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch, call

import pytest

from src.generate.hook_gen import (
    generate_hook,
    fill_hook_slot,
    _FALLBACK_HOOK,
    _TEMPERATURE,
    _build_prompt,
    _load_prompt_template,
)
from src.common.exceptions import VendorError


# ---------------------------------------------------------------------------
# 테스트 픽스처
# ---------------------------------------------------------------------------

def _make_client(return_value: str = "이거 진짜 달라졌어 ✨") -> MagicMock:
    """모킹된 VendorClient 반환."""
    client = MagicMock()
    client.generate_text.return_value = return_value
    return client


def _make_profile() -> dict:
    """최소한의 유효 style_profile dict 반환."""
    return {
        "audio": {
            "music_mood": "soft_upbeat_aesthetic",
        },
        "visual": {
            "color_grade": "warm_soft_pastel",
            "lighting": "natural_window_soft",
        },
        "narrative": {
            "beats": [
                {
                    "role": "hook",
                    "start_sec": 0.0,
                    "end_sec": 2.5,
                    "shot_type": "closeup_product",
                    "intent": "첫 3초 안에 시청자를 잡아라",
                },
                {
                    "role": "product_hero",
                    "start_sec": 2.5,
                    "end_sec": 6.0,
                    "shot_type": "flat_lay",
                    "intent": "제품 쇼케이스",
                },
            ]
        },
        "captions": {
            "slots": [
                {
                    "name": "title_hook",
                    "anchor": "top_center",
                    "font_style": "white_semibold",
                    "size_pct": 6.0,
                    "appear_sec": 0.3,
                    "duration_sec": 3.0,
                    "emoji_palette": ["✨"],
                    "is_hook": True,
                },
                {
                    "name": "subtitle_product",
                    "anchor": "top_center",
                    "font_style": "white_regular",
                    "size_pct": 3.5,
                    "appear_sec": 2.5,
                    "duration_sec": 4.0,
                    "emoji_palette": [],
                    "is_hook": False,
                },
                {
                    "name": "rolling_caption",
                    "anchor": "lower_third",
                    "font_style": "white_medium",
                    "size_pct": 3.0,
                    "appear_sec": 5.0,
                    "duration_sec": 5.0,
                    "emoji_palette": ["💖"],
                    "is_hook": False,
                },
            ]
        },
    }


def _make_brief(value: str = "글로우 세럼", kind: str = "text") -> dict:
    """최소한의 brief dict 반환."""
    return {
        "user_input": {"kind": kind, "value": value},
        "profile_path": "profiles/test.json",
    }


# ---------------------------------------------------------------------------
# generate_hook: 기본 동작 테스트
# ---------------------------------------------------------------------------

class TestGenerateHook:
    def test_returns_string(self):
        client = _make_client("이거 진짜 피부 달라졌어 ✨")
        result = generate_hook(client, _make_brief(), _make_profile())
        assert isinstance(result, str)

    def test_returns_stripped_text(self):
        client = _make_client("  훅 텍스트  ")
        result = generate_hook(client, _make_brief(), _make_profile())
        assert result == "훅 텍스트"

    def test_calls_generate_text_once_on_success(self):
        client = _make_client("훅")
        generate_hook(client, _make_brief(), _make_profile())
        assert client.generate_text.call_count == 1

    def test_temperature_at_least_0_8(self):
        """generate_text에 전달되는 temperature는 항상 ≥ 0.8 (요구사항 8.2)."""
        client = _make_client("훅")
        generate_hook(client, _make_brief(), _make_profile())
        _, kwargs = client.generate_text.call_args
        assert kwargs["temperature"] >= 0.8

    def test_explicit_seed_is_passed(self):
        """명시적 seed가 전달되면 그 seed가 generate_text에 사용된다."""
        client = _make_client("훅")
        generate_hook(client, _make_brief(), _make_profile(), seed=12345)
        _, kwargs = client.generate_text.call_args
        assert kwargs["seed"] == 12345

    def test_no_explicit_seed_generates_random(self):
        """seed=None이면 generate_text에 정수 seed가 전달된다."""
        client = _make_client("훅")
        generate_hook(client, _make_brief(), _make_profile())
        _, kwargs = client.generate_text.call_args
        assert isinstance(kwargs["seed"], int)
        assert 0 <= kwargs["seed"] <= 2**31 - 1

    def test_consecutive_calls_use_different_seeds(self):
        """seed 미지정 시 연속 호출에서 seed가 서로 다를 가능성이 높다 (요구사항 8.2).
        
        2회 호출에서 seed가 우연히 같을 수도 있으므로 10회 반복해 적어도 한 번은 달라야 한다.
        """
        client = _make_client("훅")
        seeds = set()
        for _ in range(10):
            generate_hook(client, _make_brief(), _make_profile())
        calls = client.generate_text.call_args_list
        for c in calls:
            seeds.add(c[1]["seed"])
        # 10회 중 적어도 2개의 서로 다른 seed가 사용됐어야 한다
        assert len(seeds) > 1


# ---------------------------------------------------------------------------
# generate_hook: 폴백 및 재시도 테스트
# ---------------------------------------------------------------------------

class TestGenerateHookRetry:
    def test_retries_on_vendor_error(self):
        """VendorError 발생 시 재시도한다."""
        client = MagicMock()
        client.generate_text.side_effect = [
            VendorError("실패", vendor="Gemini", operation="generate_text"),
            "훅 텍스트 ✨",
        ]
        result = generate_hook(client, _make_brief(), _make_profile())
        assert result == "훅 텍스트 ✨"
        assert client.generate_text.call_count == 2

    def test_returns_fallback_after_all_retries_exhausted(self):
        """3회 모두 실패하면 폴백 훅을 반환한다 (요구사항 8.6)."""
        client = MagicMock()
        client.generate_text.side_effect = VendorError(
            "항상 실패", vendor="Gemini", operation="generate_text"
        )
        result = generate_hook(client, _make_brief(), _make_profile())
        assert result == _FALLBACK_HOOK
        assert client.generate_text.call_count == 3

    def test_fallback_on_empty_response(self):
        """빈 문자열 응답이 3회 반복되면 폴백 훅을 반환한다."""
        client = MagicMock()
        client.generate_text.return_value = "   "  # 공백만
        result = generate_hook(client, _make_brief(), _make_profile())
        assert result == _FALLBACK_HOOK
        assert client.generate_text.call_count == 3

    def test_fallback_is_non_empty_string(self):
        """폴백 훅 텍스트는 비어 있지 않다."""
        assert isinstance(_FALLBACK_HOOK, str)
        assert _FALLBACK_HOOK.strip() != ""

    def test_warning_logged_on_fallback(self):
        """폴백 사용 시 경고가 기록된다 (요구사항 8.6)."""
        client = MagicMock()
        client.generate_text.side_effect = VendorError(
            "실패", vendor="Gemini", operation="generate_text"
        )
        with patch("src.generate.hook_gen.logger") as mock_logger:
            generate_hook(client, _make_brief(), _make_profile())
        # warning 호출 중 폴백 관련 메시지가 있어야 한다
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("폴백" in msg for msg in warning_calls)


# ---------------------------------------------------------------------------
# generate_hook: 프롬프트 구성 테스트
# ---------------------------------------------------------------------------

class TestGenerateHookPrompt:
    def test_prompt_contains_product_subject(self):
        """generate_text에 전달되는 프롬프트에 제품/주제가 포함된다."""
        client = _make_client("훅")
        brief = _make_brief(value="글로우 세럼")
        generate_hook(client, brief, _make_profile())
        prompt_arg = client.generate_text.call_args[0][0]
        assert "글로우 세럼" in prompt_arg

    def test_prompt_contains_music_mood(self):
        """프롬프트에 music_mood가 포함된다."""
        client = _make_client("훅")
        profile = _make_profile()
        profile["audio"]["music_mood"] = "energetic_pop_beat"
        generate_hook(client, _make_brief(), profile)
        prompt_arg = client.generate_text.call_args[0][0]
        assert "energetic_pop_beat" in prompt_arg

    def test_prompt_contains_hook_intent(self):
        """프롬프트에 hook beat의 intent가 포함된다."""
        client = _make_client("훅")
        profile = _make_profile()
        generate_hook(client, _make_brief(), profile)
        prompt_arg = client.generate_text.call_args[0][0]
        assert "첫 3초 안에 시청자를 잡아라" in prompt_arg

    def test_prompt_uses_default_intent_when_no_hook_beat(self):
        """hook role beat가 없으면 기본 intent를 사용한다."""
        client = _make_client("훅")
        profile = _make_profile()
        profile["narrative"]["beats"] = []  # hook beat 없음
        generate_hook(client, _make_brief(), profile)
        prompt_arg = client.generate_text.call_args[0][0]
        assert isinstance(prompt_arg, str)
        assert len(prompt_arg) > 0

    def test_image_input_uses_stem_as_subject(self):
        """image 입력인 경우 파일명(stem)을 제품명으로 사용한다."""
        client = _make_client("훅")
        brief = _make_brief(value="assets/product_glow.png", kind="image")
        generate_hook(client, brief, _make_profile())
        prompt_arg = client.generate_text.call_args[0][0]
        assert "product_glow" in prompt_arg


# ---------------------------------------------------------------------------
# fill_hook_slot 테스트
# ---------------------------------------------------------------------------

class TestFillHookSlot:
    def test_fills_is_hook_true_slot(self):
        """is_hook=true 슬롯에 훅 텍스트가 채워진다 (요구사항 8.4)."""
        profile = _make_profile()
        filled = fill_hook_slot(profile, "이거 진짜 달라졌어 ✨")
        hook_slots = [
            s for s in filled["captions"]["slots"] if s.get("is_hook") is True
        ]
        assert len(hook_slots) == 1
        assert hook_slots[0]["text"] == "이거 진짜 달라졌어 ✨"

    def test_does_not_modify_non_hook_slots(self):
        """is_hook=false 슬롯은 변경되지 않는다 (요구사항 8.4)."""
        profile = _make_profile()
        filled = fill_hook_slot(profile, "훅 텍스트")
        non_hook_slots = [
            s for s in filled["captions"]["slots"] if s.get("is_hook") is not True
        ]
        for slot in non_hook_slots:
            assert "text" not in slot

    def test_returns_deep_copy(self):
        """원본 profile이 변경되지 않는다."""
        profile = _make_profile()
        original_slots = copy.deepcopy(profile["captions"]["slots"])
        fill_hook_slot(profile, "훅 텍스트")
        # 원본의 is_hook=true 슬롯에 text 키가 추가되지 않아야 한다
        for orig_slot, profile_slot in zip(original_slots, profile["captions"]["slots"]):
            assert orig_slot == profile_slot

    def test_no_hook_slots_returns_unchanged(self):
        """is_hook=true 슬롯이 없으면 프로파일을 그대로 반환한다."""
        profile = _make_profile()
        for slot in profile["captions"]["slots"]:
            slot["is_hook"] = False
        filled = fill_hook_slot(profile, "훅")
        for slot in filled["captions"]["slots"]:
            assert "text" not in slot

    def test_multiple_hook_slots_all_filled(self):
        """is_hook=true 슬롯이 여러 개면 모두 채워진다."""
        profile = _make_profile()
        profile["captions"]["slots"][0]["is_hook"] = True
        profile["captions"]["slots"][1]["is_hook"] = True
        filled = fill_hook_slot(profile, "훅 텍스트")
        hook_slots = [
            s for s in filled["captions"]["slots"] if s.get("is_hook") is True
        ]
        assert len(hook_slots) == 2
        for slot in hook_slots:
            assert slot["text"] == "훅 텍스트"

    def test_empty_slots_list(self):
        """slots가 비어 있어도 오류 없이 동작한다."""
        profile = _make_profile()
        profile["captions"]["slots"] = []
        filled = fill_hook_slot(profile, "훅")
        assert filled["captions"]["slots"] == []

    def test_missing_captions_key(self):
        """captions 키가 없어도 오류 없이 동작한다."""
        profile = {"narrative": {}, "visual": {}, "audio": {}}
        filled = fill_hook_slot(profile, "훅")
        assert "captions" not in filled or filled.get("captions", {}).get("slots", []) == []

    def test_preserves_other_profile_sections(self):
        """captions 외 다른 섹션은 변경되지 않는다."""
        profile = _make_profile()
        filled = fill_hook_slot(profile, "훅")
        assert filled["visual"] == profile["visual"]
        assert filled["audio"] == profile["audio"]
        assert filled["narrative"] == profile["narrative"]


# ---------------------------------------------------------------------------
# _load_prompt_template 테스트
# ---------------------------------------------------------------------------

class TestLoadPromptTemplate:
    def test_returns_string(self):
        template = _load_prompt_template()
        assert isinstance(template, str)
        assert len(template) > 0

    def test_contains_placeholders(self):
        """템플릿에 필수 치환 변수가 포함된다."""
        template = _load_prompt_template()
        assert "{product_subject}" in template
        assert "{music_mood}" in template

    def test_raises_on_missing_file(self, tmp_path):
        """프롬프트 파일이 없으면 FileNotFoundError를 raise한다."""
        from src.generate import hook_gen as hg
        original_path = hg._HOOK_GEN_PROMPT_PATH
        hg._HOOK_GEN_PROMPT_PATH = tmp_path / "nonexistent.md"
        try:
            with pytest.raises(FileNotFoundError):
                _load_prompt_template()
        finally:
            hg._HOOK_GEN_PROMPT_PATH = original_path


# ---------------------------------------------------------------------------
# _build_prompt 테스트
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_substitutes_all_placeholders(self):
        template = (
            "Product: {product_subject}\n"
            "Music: {music_mood}\n"
            "Color: {color_grade}\n"
            "Lighting: {lighting}\n"
            "Intent: {hook_intent}"
        )
        brief = _make_brief(value="비타민 세럼")
        profile = _make_profile()
        result = _build_prompt(template, brief, profile)
        assert "비타민 세럼" in result
        assert "soft_upbeat_aesthetic" in result
        assert "warm_soft_pastel" in result
        assert "natural_window_soft" in result
        assert "첫 3초 안에 시청자를 잡아라" in result

    def test_no_remaining_placeholders(self):
        template = _load_prompt_template()
        brief = _make_brief()
        profile = _make_profile()
        result = _build_prompt(template, brief, profile)
        assert "{product_subject}" not in result
        assert "{music_mood}" not in result
        assert "{color_grade}" not in result
        assert "{lighting}" not in result
        assert "{hook_intent}" not in result

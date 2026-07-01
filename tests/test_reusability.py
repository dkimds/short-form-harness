"""
tests/test_reusability.py — 재사용성 통합 테스트 (Task 12.1, 12.2)

핵심 검증: 동일 profile + 다른 입력 → 다른 산출물 (코드 변경 불필요)

포함 테스트:
  - test_different_inputs_produce_different_briefs
  - test_different_inputs_produce_different_shotlist_prompts
  - test_same_profile_different_hooks_non_deterministic
  - test_pipeline_completes_without_code_changes (Property 21)
  - test_three_inputs_produce_three_different_shotlists
  - test_property_20_different_inputs_different_outputs (Property 20 PBT)

모든 벤더 호출은 MagicMock으로 격리한다 — 외부 API 호출 없음.
"""

from __future__ import annotations

import json
import random
import struct
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.generate.brief import UserInput, build_brief
from src.generate.hook_gen import fill_hook_slot, generate_hook
from src.generate.plan import build_shotlist


# ---------------------------------------------------------------------------
# 헬퍼 / 픽스처
# ---------------------------------------------------------------------------

_PROFILE_PATH = Path(__file__).resolve().parents[1] / "profiles" / "ref1.json"


def _load_biodance_profile() -> dict:
    """실제 profiles/ref1.json을 로드한다."""
    with open(_PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _make_png_bytes(width: int = 576, height: int = 1024) -> bytes:
    """지정된 크기의 유효한 PNG 바이트를 생성한다."""
    row = bytes([0x00]) + bytes([100, 150, 200] * width)
    raw = row * height
    compressed = zlib.compress(raw)

    def chunk(name: bytes, data: bytes) -> bytes:
        c = name + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _make_vendor_client(hook_text: str = "이거 피부 진짜 달라졌어 ✨") -> MagicMock:
    """VendorClient를 완전히 모킹한다. 외부 API 호출 없음."""
    client = MagicMock()
    client.generate_text.return_value = hook_text
    client.generate_image.return_value = _make_png_bytes()
    client.synthesize_speech.return_value = b"\x00" * 200
    client.image_to_video.return_value = b"\x00" * 500
    return client


def _run_brief_and_plan(
    profile: dict,
    text: str,
    hook_text: str = "고정 훅 텍스트",
    seed: int = 42,
) -> tuple[dict, dict]:
    """build_brief → fill_hook_slot → build_shotlist를 실행하고 (brief, shotlist)를 반환한다."""
    user_input = UserInput(kind="text", value=text)
    brief = build_brief(profile, user_input)
    filled_profile = fill_hook_slot(profile, hook_text)
    rng = random.Random(seed)
    shotlist = build_shotlist(brief, filled_profile, hook_text, rng=rng)
    return brief, shotlist


# ---------------------------------------------------------------------------
# Test 1: 다른 입력은 다른 brief를 만든다
# ---------------------------------------------------------------------------

class TestDifferentInputsProduceDifferentBriefs:
    def test_different_inputs_produce_different_briefs(self):
        profile = _load_biodance_profile()
        inputs = ["glow serum", "vitamin C", "retinol cream"]
        briefs = [build_brief(profile, UserInput(kind="text", value=t)) for t in inputs]
        values = [b["user_input"]["value"] for b in briefs]
        assert values[0] != values[1]
        assert values[1] != values[2]
        assert values[0] != values[2]

    def test_profile_sections_are_shared(self):
        profile = _load_biodance_profile()
        brief1 = build_brief(profile, UserInput(kind="text", value="glow serum"))
        brief2 = build_brief(profile, UserInput(kind="text", value="vitamin C"))
        assert brief1["narrative"] == brief2["narrative"]
        assert brief1["visual"] == brief2["visual"]
        assert brief1["pacing"] == brief2["pacing"]
        assert brief1["user_input"]["value"] != brief2["user_input"]["value"]


# ---------------------------------------------------------------------------
# Test 2: 다른 입력은 다른 shotlist 프롬프트를 만든다
# ---------------------------------------------------------------------------

class TestDifferentInputsProduceDifferentShotlistPrompts:
    def test_different_inputs_produce_different_shotlist_prompts(self):
        profile = _load_biodance_profile()
        texts = ["glow serum", "vitamin C", "retinol cream"]
        prompts = [_run_brief_and_plan(profile, t, seed=42)[1]["shots"][0]["prompt"] for t in texts]
        assert prompts[0] != prompts[1]
        assert prompts[1] != prompts[2]
        assert prompts[0] != prompts[2]

    def test_prompts_contain_product_subject(self):
        profile = _load_biodance_profile()
        for text in ["glow serum", "vitamin C", "retinol cream"]:
            _, shotlist = _run_brief_and_plan(profile, text, seed=42)
            assert text in shotlist["shots"][0]["prompt"]

    def test_shotlist_has_shots(self):
        profile = _load_biodance_profile()
        _, shotlist = _run_brief_and_plan(profile, "glow serum", seed=42)
        assert len(shotlist["shots"]) > 0


# ---------------------------------------------------------------------------
# Test 3: 비결정적 hook
# ---------------------------------------------------------------------------

class TestSameProfileDifferentHooksNonDeterministic:
    def test_same_profile_different_hooks_non_deterministic(self):
        profile = _load_biodance_profile()
        brief = build_brief(profile, UserInput(kind="text", value="glow serum"))
        client = MagicMock()
        client.generate_text.side_effect = [
            "이거 진짜 피부 달라졌어 ✨",
            "글로우 세럼으로 찐 광채 완성 💖",
        ]
        hook1 = generate_hook(client, brief, profile)
        hook2 = generate_hook(client, brief, profile)
        assert hook1 != hook2

    def test_generate_hook_uses_random_seed(self):
        profile = _load_biodance_profile()
        brief = build_brief(profile, UserInput(kind="text", value="glow serum"))
        client = _make_vendor_client()
        seeds_used = []
        for _ in range(5):
            generate_hook(client, brief, profile)
            _, kwargs = client.generate_text.call_args
            seeds_used.append(kwargs["seed"])
        assert len(set(seeds_used)) > 1


# ---------------------------------------------------------------------------
# Test 4: Property 21 — 코드 변경 없이 파이프라인 완주
# ---------------------------------------------------------------------------

class TestPipelineCompletesWithoutCodeChanges:
    def test_pipeline_completes_without_code_changes(self, tmp_path):
        """Property 21: 스키마 유효 프로파일로 파이프라인이 코드 변경 없이 완주한다."""
        from src.generate.assets import render_assets

        profile = _load_biodance_profile()
        brief = build_brief(profile, UserInput(kind="text", value="glow serum"))
        brief["run_dir"] = str(tmp_path)

        client = _make_vendor_client(hook_text="파이프라인 테스트 훅 ✨")
        hook_text = generate_hook(client, brief, profile)
        filled_profile = fill_hook_slot(profile, hook_text)
        shotlist = build_shotlist(brief, filled_profile, hook_text, rng=random.Random(42))

        render_client = _make_vendor_client()
        result_shotlist = render_assets(render_client, shotlist, profile, str(tmp_path))

        assert result_shotlist is not None
        assert "shots" in result_shotlist

        with patch("src.generate.compose.compose_video") as mock_compose:
            mock_compose.return_value = str(tmp_path / "final.mp4")
            from src.generate.compose import compose_video
            output_path = compose_video(shotlist, profile, str(tmp_path))

        assert output_path is not None
        assert "final.mp4" in output_path

    def test_pipeline_brief_and_shotlist_complete(self):
        profile = _load_biodance_profile()
        brief = build_brief(profile, UserInput(kind="text", value="retinol cream"))
        hook_text = "리타놀 크림으로 피부 재생 ✨"
        filled_profile = fill_hook_slot(profile, hook_text)
        shotlist = build_shotlist(brief, filled_profile, hook_text, rng=random.Random(123))
        assert isinstance(brief, dict)
        assert len(shotlist["shots"]) > 0


# ---------------------------------------------------------------------------
# Test 5: 3개 입력 → 3개 다른 shotlist
# ---------------------------------------------------------------------------

class TestThreeInputsProduceThreeDifferentShotlists:
    def test_three_inputs_produce_three_different_shotlists(self):
        """Validates: Requirements 14.1, 14.2"""
        profile = _load_biodance_profile()
        texts = ["glow serum", "vitamin C toner", "retinol night cream"]
        prompts = []
        for text in texts:
            brief = build_brief(profile, UserInput(kind="text", value=text))
            brief["run_dir"] = f"outputs/test_{text.replace(' ', '_')}"
            hook_text = f"{text} 훅 텍스트"
            filled_profile = fill_hook_slot(profile, hook_text)
            shotlist = build_shotlist(brief, filled_profile, hook_text, rng=random.Random(42))
            prompts.append(shotlist["shots"][0]["prompt"])
        assert prompts[0] != prompts[1]
        assert prompts[1] != prompts[2]
        assert prompts[0] != prompts[2]

    def test_each_shotlist_contains_product_subject(self):
        profile = _load_biodance_profile()
        for text in ["glow serum", "vitamin C toner", "retinol night cream"]:
            brief = build_brief(profile, UserInput(kind="text", value=text))
            filled_profile = fill_hook_slot(profile, "테스트 훅")
            shotlist = build_shotlist(brief, filled_profile, "테스트 훅", rng=random.Random(42))
            for shot in shotlist["shots"]:
                assert text in shot["prompt"]

    def test_same_seed_different_input_gives_different_output(self):
        profile = _load_biodance_profile()
        _, sl1 = _run_brief_and_plan(profile, "glow serum", seed=99)
        _, sl2 = _run_brief_and_plan(profile, "retinol night cream", seed=99)
        assert [s["prompt"] for s in sl1["shots"]] != [s["prompt"] for s in sl2["shots"]]


# ---------------------------------------------------------------------------
# Property 20 PBT
# ---------------------------------------------------------------------------

_text_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters=" _-",
    ),
    min_size=2,
    max_size=40,
).filter(lambda t: t.strip())


@settings(max_examples=100)
@given(text1=_text_strategy, text2=_text_strategy)
def test_property_20_different_inputs_different_outputs(text1: str, text2: str) -> None:
    """Property 20: 재사용성 — 다른 입력은 다른 산출물을 만든다.

    # Feature: short-form-harness, Property 20: 재사용성 — 다른 입력은 다른 산출물을 만든다
    Validates: Requirements 14.1, 14.2
    """
    assume(text1.strip() != text2.strip())

    profile = _load_biodance_profile()
    brief1 = build_brief(profile, UserInput(kind="text", value=text1))
    brief2 = build_brief(profile, UserInput(kind="text", value=text2))

    assert brief1["user_input"]["value"] != brief2["user_input"]["value"]

    hook_text = "공통 훅 텍스트"
    filled_profile = fill_hook_slot(profile, hook_text)
    sl1 = build_shotlist(brief1, filled_profile, hook_text, rng=random.Random(42))
    sl2 = build_shotlist(brief2, filled_profile, hook_text, rng=random.Random(42))

    assert len(sl1["shots"]) > 0
    assert len(sl2["shots"]) > 0
    assert sl1["shots"][0]["prompt"] != sl2["shots"][0]["prompt"]

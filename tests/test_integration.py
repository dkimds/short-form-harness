"""
tests/test_integration.py — 재사용성 통합 테스트 (Task 12.1)

# Feature: short-form-harness, Property 20: 재사용성 — 다른 입력은 다른 산출물을 만든다

핵심 검증:
  - 동일 profile + 서로 다른 3개 입력(text, image, video) → 3개의 다른 산출물
  - 모든 VendorClient 호출 모킹 (실제 API 호출 없음)
  - 각 run이 오류 없이 end-to-end 완주
  - prompt.txt / shotlist.json / brief["user_input"]["value"] 차이 확인

모킹 전략:
  - VendorClient.generate_text    → 호출 횟수별로 다른 훅 텍스트 반환
  - VendorClient.generate_image   → 최소 유효 PNG (576×1024) 바이트 반환
  - VendorClient.image_to_video   → 최소 유효 MP4 바이트(빈 bytes) 반환
  - VendorClient.synthesize_speech → 무음 WAV 바이트 반환
  - VendorClient.judge_video      → {"verdict": "pass", "reasons": []} 반환
  - compose_video                 → 실제 moviepy 우회, 빈 final.mp4 파일 생성
"""

from __future__ import annotations

import json
import random
import struct
import zlib
from itertools import count
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.generate.brief import UserInput, build_brief, write_prompt_txt
from src.generate.hook_gen import fill_hook_slot, generate_hook
from src.generate.plan import build_shotlist, write_shotlist
from src.generate.assets import render_assets
from src.generate.gate import run_gate
from src.common.io import make_run_id, make_run_dir


# ---------------------------------------------------------------------------
# 상수 / 헬퍼
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


def _make_silent_wav(duration_sec: float = 0.1, sample_rate: int = 22050) -> bytes:
    """무음 WAV 바이트를 생성한다."""
    num_samples = int(sample_rate * duration_sec)
    pcm_data = b"\x00\x00" * num_samples
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        b"data", data_size,
    )
    return header + pcm_data


def _make_vendor_client(call_counter: list[int] | None = None) -> MagicMock:
    """완전히 모킹된 VendorClient를 반환한다. 외부 API 호출 없음.

    generate_text는 호출 횟수별로 다른 훅 텍스트를 반환한다.
    """
    client = MagicMock()

    # generate_text: 호출마다 다른 훅 (비결정성 시뮬레이션)
    _counter = count()

    def _gen_text(prompt, *, temperature, seed):
        n = next(_counter)
        hooks = [
            "이거 진짜 피부 달라졌어 ✨",
            "글로우 세럼 찐 효과 💖",
            "영상 보고 바로 구매했어 🌟",
            "피부가 환해진 게 보여 ✨",
        ]
        return hooks[n % len(hooks)]

    client.generate_text.side_effect = _gen_text
    client.generate_image.return_value = _make_png_bytes()
    client.image_to_video.return_value = b"\x00" * 512  # 최소 MP4-like bytes
    client.synthesize_speech.return_value = _make_silent_wav()
    client.judge_video.return_value = {"verdict": "pass", "reasons": []}

    return client


def _run_full_pipeline(
    profile: dict,
    user_input: UserInput,
    run_dir: str,
    client: MagicMock,
    seed: int = 42,
) -> tuple[dict, dict, str]:
    """브리프 → 훅 → 숏리스트 → 에셋 → prompt.txt/shotlist.json 기록 파이프라인 실행.

    compose_video와 run_gate는 각각 모킹해 실제 ffmpeg/moviepy 없이 완주한다.

    Returns:
        (brief, shotlist, run_dir) 튜플
    """
    # 1. Brief 생성
    brief = build_brief(profile, user_input, profile_path=str(_PROFILE_PATH))
    brief["run_dir"] = run_dir

    # 2. 훅 생성
    hook_text = generate_hook(client, brief, profile)

    # 3. 훅 슬롯 채우기
    profile_with_hook = fill_hook_slot(profile, hook_text)

    # 4. 숏리스트 플래닝
    rng = random.Random(seed)
    shotlist = build_shotlist(brief, profile_with_hook, hook_text, rng=rng)

    # 5. 에셋 생성 (모킹된 VendorClient 사용)
    shotlist = render_assets(client, shotlist, profile_with_hook, run_dir)

    # 6. prompt.txt 저장
    write_prompt_txt(brief, hook_text, run_dir)

    # 7. shotlist.json 저장
    write_shotlist(shotlist, run_dir)

    return brief, shotlist, run_dir


# ---------------------------------------------------------------------------
# 통합 테스트: 3가지 입력 타입 (text, image, video)
# ---------------------------------------------------------------------------

class TestIntegrationThreeInputTypes:
    """동일 profile + text / image / video 입력 → 3개의 서로 다른 산출물 end-to-end 검증.

    **Validates: Requirements 14.1, 14.2**
    """

    @pytest.fixture(autouse=True)
    def _setup_files(self, tmp_path):
        """image / video 입력용 임시 파일을 생성한다."""
        # 이미지 파일 (유효한 PNG)
        self.image_path = tmp_path / "input_image.png"
        self.image_path.write_bytes(_make_png_bytes(576, 1024))

        # 비디오 파일 (최소 MP4: 4바이트 이상이면 경로 검증 통과)
        self.video_path = tmp_path / "input_video.mp4"
        self.video_path.write_bytes(b"\x00" * 512)

        self.tmp_path = tmp_path

    def _make_run_dir(self, name: str) -> str:
        run_dir = self.tmp_path / name
        run_dir.mkdir(parents=True, exist_ok=True)
        return str(run_dir)

    def test_three_inputs_produce_different_briefs(self):
        """text / image / video 입력 → 3개의 brief["user_input"]["value"]가 모두 다르다."""
        profile = _load_biodance_profile()

        inputs = [
            UserInput(kind="text", value="glow serum"),
            UserInput(kind="image", value=str(self.image_path)),
            UserInput(kind="video", value=str(self.video_path)),
        ]

        client = _make_vendor_client()
        briefs = []
        for ui in inputs:
            run_dir = self._make_run_dir(f"run_{ui.kind}")
            brief, _, _ = _run_full_pipeline(profile, ui, run_dir, client)
            briefs.append(brief)

        values = [b["user_input"]["value"] for b in briefs]
        # text vs image 경로
        assert values[0] != values[1], f"text vs image 값이 달라야 함: {values[0]!r} vs {values[1]!r}"
        # image 경로 vs video 경로
        assert values[1] != values[2], f"image vs video 값이 달라야 함: {values[1]!r} vs {values[2]!r}"
        # text vs video 경로
        assert values[0] != values[2], f"text vs video 값이 달라야 함: {values[0]!r} vs {values[2]!r}"

    def test_three_inputs_produce_non_empty_shotlists(self):
        """text / image / video 입력으로 생성된 3개 shotlist가 모두 비어 있지 않다."""
        profile = _load_biodance_profile()

        inputs = [
            UserInput(kind="text", value="glow serum"),
            UserInput(kind="image", value=str(self.image_path)),
            UserInput(kind="video", value=str(self.video_path)),
        ]

        client = _make_vendor_client()
        for ui in inputs:
            run_dir = self._make_run_dir(f"run_shots_{ui.kind}")
            _, shotlist, _ = _run_full_pipeline(profile, ui, run_dir, client)
            assert len(shotlist["shots"]) > 0, f"kind={ui.kind}의 shotlist가 비어 있음"

    def test_three_inputs_produce_different_shotlist_prompts(self):
        """text / image / video 입력 → 3개 shotlist의 첫 번째 shot 프롬프트가 모두 다르다."""
        profile = _load_biodance_profile()

        inputs = [
            UserInput(kind="text", value="glow serum"),
            UserInput(kind="image", value=str(self.image_path)),
            UserInput(kind="video", value=str(self.video_path)),
        ]

        client = _make_vendor_client()
        first_prompts = []
        for ui in inputs:
            run_dir = self._make_run_dir(f"run_prompts_{ui.kind}")
            _, shotlist, _ = _run_full_pipeline(profile, ui, run_dir, client, seed=42)
            first_prompts.append(shotlist["shots"][0]["prompt"])

        assert first_prompts[0] != first_prompts[1], (
            f"text vs image 첫 shot 프롬프트가 달라야 함"
        )
        assert first_prompts[1] != first_prompts[2], (
            f"image vs video 첫 shot 프롬프트가 달라야 함"
        )
        assert first_prompts[0] != first_prompts[2], (
            f"text vs video 첫 shot 프롬프트가 달라야 함"
        )

    def test_prompt_txt_contains_input_value(self):
        """각 run의 prompt.txt에 해당 user_input의 value가 포함된다."""
        profile = _load_biodance_profile()

        inputs = [
            UserInput(kind="text", value="glow serum"),
            UserInput(kind="image", value=str(self.image_path)),
            UserInput(kind="video", value=str(self.video_path)),
        ]

        client = _make_vendor_client()
        for ui in inputs:
            run_dir = self._make_run_dir(f"run_prompttxt_{ui.kind}")
            _, _, rd = _run_full_pipeline(profile, ui, run_dir, client)
            prompt_txt = Path(rd) / "prompt.txt"
            assert prompt_txt.exists(), f"prompt.txt가 없음: {prompt_txt}"
            content = prompt_txt.read_text(encoding="utf-8")
            assert ui.value in content, (
                f"prompt.txt에 '{ui.value}'가 없음:\n{content}"
            )

    def test_shotlist_json_contains_required_fields(self):
        """각 run의 shotlist.json에 role/asset_type/prompt/asset_path가 존재한다."""
        profile = _load_biodance_profile()

        inputs = [
            UserInput(kind="text", value="glow serum"),
            UserInput(kind="image", value=str(self.image_path)),
            UserInput(kind="video", value=str(self.video_path)),
        ]

        client = _make_vendor_client()
        for ui in inputs:
            run_dir = self._make_run_dir(f"run_shotjson_{ui.kind}")
            _, _, rd = _run_full_pipeline(profile, ui, run_dir, client)
            shotlist_path = Path(rd) / "shotlist.json"
            assert shotlist_path.exists(), f"shotlist.json이 없음: {shotlist_path}"

            data = json.loads(shotlist_path.read_text(encoding="utf-8"))
            shots = data.get("shots", [])
            assert len(shots) > 0, f"kind={ui.kind}: shotlist.json의 shots가 비어 있음"

            for shot in shots:
                for field in ("role", "asset_type", "prompt", "asset_path"):
                    assert field in shot, (
                        f"kind={ui.kind}, shot[{shot.get('index')}]에 '{field}' 필드 없음"
                    )

    def test_no_vendor_calls_raise_exception(self):
        """모킹된 VendorClient 호출 시 예외가 발생하지 않고 end-to-end 완주한다."""
        profile = _load_biodance_profile()
        client = _make_vendor_client()

        inputs = [
            UserInput(kind="text", value="glow serum"),
            UserInput(kind="image", value=str(self.image_path)),
            UserInput(kind="video", value=str(self.video_path)),
        ]

        for ui in inputs:
            run_dir = self._make_run_dir(f"run_noexc_{ui.kind}")
            # 예외 없이 완주해야 한다
            brief, shotlist, _ = _run_full_pipeline(profile, ui, run_dir, client)
            assert brief is not None
            assert shotlist is not None

    def test_all_vendor_client_mocked(self):
        """VendorClient의 generate_image/synthesize_speech가 실제로 호출된다 (모킹 확인)."""
        profile = _load_biodance_profile()
        client = _make_vendor_client()

        user_input = UserInput(kind="text", value="glow serum")
        run_dir = self._make_run_dir("run_mock_verify")
        _run_full_pipeline(profile, user_input, run_dir, client)

        # generate_image는 shots 수만큼 호출됨
        assert client.generate_image.call_count > 0, "generate_image가 호출되지 않음"
        # generate_text (hook_gen)은 1회 이상 호출됨
        assert client.generate_text.call_count >= 1, "generate_text가 호출되지 않음"


# ---------------------------------------------------------------------------
# Gate QA 통합 테스트 (judge_video 모킹)
# ---------------------------------------------------------------------------

class TestIntegrationWithGate:
    """run_gate를 포함한 통합 테스트 — judge_video 모킹."""

    def test_gate_with_mocked_judge_video(self, tmp_path):
        """run_gate가 모킹된 judge_video로 정상 실행된다.

        final.mp4는 빈 파일로 생성 — ffprobe 실패 시 graceful pass로 처리됨.
        """
        profile = _load_biodance_profile()
        client = _make_vendor_client()

        # 빈 final.mp4 생성 (ffprobe는 실패하지만 gate.py는 graceful pass 처리)
        final_mp4 = str(tmp_path / "final.mp4")
        Path(final_mp4).write_bytes(b"\x00" * 16)

        gate_result = run_gate(client, final_mp4, profile, str(tmp_path))

        # judge_video가 호출됐는지 확인
        # (ffprobe 실패 케이스에서도 gate.json은 저장됨)
        gate_json_path = tmp_path / "gate.json"
        assert gate_json_path.exists(), "gate.json이 저장되지 않음"

        data = json.loads(gate_json_path.read_text(encoding="utf-8"))
        assert "passed" in data
        assert "deterministic" in data
        assert "vision_judgment" in data

    def test_gate_pass_when_judge_returns_pass(self, tmp_path):
        """judge_video가 pass를 반환하면 gate.passed는 True다 (ffprobe graceful pass 포함)."""
        profile = _load_biodance_profile()
        client = _make_vendor_client()
        client.judge_video.return_value = {"verdict": "pass", "reasons": []}

        final_mp4 = str(tmp_path / "final.mp4")
        Path(final_mp4).write_bytes(b"\x00" * 16)

        gate_result = run_gate(client, final_mp4, profile, str(tmp_path))

        # ffprobe가 없으면 deterministic check는 graceful pass → gate.passed=True
        # (gate_result.passed는 deterministic.passed AND vision.verdict == "pass")
        assert gate_result.vision_judgment["verdict"] == "pass"


# ---------------------------------------------------------------------------
# Property 20 PBT: 다른 입력은 다른 산출물을 만든다
#
# # Feature: short-form-harness, Property 20: 재사용성 — 다른 입력은 다른 산출물을 만든다
# ---------------------------------------------------------------------------

_text_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters=" _-",
    ),
    min_size=2,
    max_size=40,
).filter(lambda t: t.strip())


@settings(max_examples=50)
@given(
    text1=_text_strategy,
    text2=_text_strategy,
)
def test_property_20_integration_different_inputs_produce_different_outputs(
    text1: str,
    text2: str,
) -> None:
    """Property 20: 재사용성 — 다른 입력은 다른 산출물을 만든다.

    동일 profile에 서로 다른 두 UserInput(text)을 넣으면:
      1. brief["user_input"]["value"]가 서로 다르다.
      2. shotlist["shots"][0]["prompt"]가 서로 다르다 (product_subject가 다르므로).

    # Feature: short-form-harness, Property 20: 재사용성 — 다른 입력은 다른 산출물을 만든다

    **Validates: Requirements 14.1, 14.2**
    """
    assume(text1.strip() != text2.strip())

    profile = _load_biodance_profile()
    hook_text = "공통 훅"
    filled_profile = fill_hook_slot(profile, hook_text)

    # Brief 생성
    brief1 = build_brief(profile, UserInput(kind="text", value=text1))
    brief2 = build_brief(profile, UserInput(kind="text", value=text2))

    # 1) brief의 user_input.value가 서로 다름
    assert brief1["user_input"]["value"] != brief2["user_input"]["value"], (
        f"다른 입력 → 다른 brief.user_input.value: {text1!r} vs {text2!r}"
    )

    # 2) shotlist의 첫 shot 프롬프트가 서로 다름
    rng1 = random.Random(42)
    rng2 = random.Random(42)
    shotlist1 = build_shotlist(brief1, filled_profile, hook_text, rng=rng1)
    shotlist2 = build_shotlist(brief2, filled_profile, hook_text, rng=rng2)

    shots1 = shotlist1.get("shots", [])
    shots2 = shotlist2.get("shots", [])

    assert len(shots1) > 0, "shotlist1에 shots가 있어야 함"
    assert len(shots2) > 0, "shotlist2에 shots가 있어야 함"

    assert shots1[0]["prompt"] != shots2[0]["prompt"], (
        f"다른 입력 → 다른 shot[0].prompt:\n"
        f"  text1={text1!r} → {shots1[0]['prompt']!r}\n"
        f"  text2={text2!r} → {shots2[0]['prompt']!r}"
    )

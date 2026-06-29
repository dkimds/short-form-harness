"""
tests/test_assets.py — assets.py 단위 테스트

render_assets, 폴백 이미지 생성, 비율 검증, 보이스오버 생성 등을 검증한다.
VendorClient는 모킹해 외부 API를 호출하지 않는다.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from src.common.exceptions import VendorError
from src.generate.assets import (
    render_assets,
    _verify_ratio,
    _generate_fallback_image,
    _build_vo_text,
    _strip_emoji,
    _get_fallback_bg_color,
    _FALLBACK_WIDTH,
    _FALLBACK_HEIGHT,
    _TARGET_RATIO,
    _RATIO_TOLERANCE,
)


# ---------------------------------------------------------------------------
# 테스트 헬퍼 / 픽스처
# ---------------------------------------------------------------------------

def _make_png_bytes(width: int, height: int) -> bytes:
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


def _make_client(image_bytes: bytes | None = None, raises: Exception | None = None) -> MagicMock:
    """모킹된 VendorClient를 반환한다."""
    client = MagicMock()
    if raises is not None:
        client.generate_image.side_effect = raises
    else:
        client.generate_image.return_value = image_bytes or _make_png_bytes(576, 1024)
    client.synthesize_speech.return_value = b"\x00" * 100
    return client


def _make_shotlist(n: int = 2, asset_type: str = "imagen_image") -> dict:
    """n개의 숏을 가진 최소 shotlist dict를 반환한다."""
    return {
        "run_id": "test_run",
        "shots": [
            {
                "index": i,
                "role": "hook" if i == 0 else "application",
                "asset_type": asset_type,
                "duration_sec": 1.5,
                "prompt": f"Test prompt for shot {i}",
                "asset_path": "",
            }
            for i in range(n)
        ],
    }


def _make_profile(has_voiceover: bool = False) -> dict:
    """최소 style_profile dict를 반환한다."""
    return {
        "audio": {
            "has_voiceover": has_voiceover,
            "music_mood": "upbeat_light",
            "target_lufs": -23.0,
        },
        "visual": {
            "accent_color": "#ED99BE",
            "color_grade": "warm_soft",
            "lighting": "natural_window",
        },
        "captions": {
            "slots": [
                {
                    "name": "title_hook",
                    "is_hook": True,
                    "appear_sec": 0.0,
                    "duration_sec": 3.0,
                }
            ]
        },
        "format": {"aspect_ratio": "9:16", "resolution": "576x1024"},
    }


# ---------------------------------------------------------------------------
# render_assets: 기본 동작 테스트
# ---------------------------------------------------------------------------

class TestRenderAssetsBasic:
    def test_returns_shotlist_dict(self, tmp_path):
        """render_assets는 shotlist dict를 반환한다."""
        client = _make_client()
        shotlist = _make_shotlist(2)
        result = render_assets(client, shotlist, _make_profile(), str(tmp_path))
        assert isinstance(result, dict)
        assert "shots" in result

    def test_returns_same_shotlist_object(self, tmp_path):
        """render_assets는 동일한 shotlist 객체를 in-place 수정 후 반환한다."""
        client = _make_client()
        shotlist = _make_shotlist(2)
        result = render_assets(client, shotlist, _make_profile(), str(tmp_path))
        assert result is shotlist

    def test_asset_paths_set_for_imagen_shots(self, tmp_path):
        """imagen_image 숏들의 asset_path가 채워진다 (요구사항 10.4)."""
        client = _make_client()
        shotlist = _make_shotlist(3)
        render_assets(client, shotlist, _make_profile(), str(tmp_path))
        for shot in shotlist["shots"]:
            assert shot["asset_path"] != "", f"shot {shot['index']} asset_path is empty"

    def test_image_files_created(self, tmp_path):
        """생성된 이미지 파일이 실제로 디스크에 저장된다 (요구사항 10.4)."""
        client = _make_client()
        shotlist = _make_shotlist(2)
        render_assets(client, shotlist, _make_profile(), str(tmp_path))
        for shot in shotlist["shots"]:
            assert Path(shot["asset_path"]).exists()

    def test_asset_path_naming_convention(self, tmp_path):
        """파일명 형식이 shot_{index:02d}.png 이다."""
        client = _make_client()
        shotlist = _make_shotlist(3)
        render_assets(client, shotlist, _make_profile(), str(tmp_path))
        for shot in shotlist["shots"]:
            idx = shot["index"]
            expected_name = f"shot_{idx:02d}.png"
            assert Path(shot["asset_path"]).name == expected_name

    def test_generate_image_called_per_shot(self, tmp_path):
        """숏 개수만큼 generate_image가 호출된다 (요구사항 10.1)."""
        client = _make_client()
        shotlist = _make_shotlist(3)
        render_assets(client, shotlist, _make_profile(), str(tmp_path))
        assert client.generate_image.call_count == 3

    def test_generate_image_called_with_aspect_ratio(self, tmp_path):
        """generate_image는 aspect_ratio="9:16"으로 호출된다 (요구사항 10.5)."""
        client = _make_client()
        shotlist = _make_shotlist(1)
        render_assets(client, shotlist, _make_profile(), str(tmp_path))
        _, kwargs = client.generate_image.call_args
        assert kwargs["aspect_ratio"] == "9:16"

    def test_generate_image_called_with_prompt(self, tmp_path):
        """generate_image는 숏의 prompt를 첫 번째 인자로 받는다."""
        client = _make_client()
        shotlist = _make_shotlist(1)
        expected_prompt = shotlist["shots"][0]["prompt"]
        render_assets(client, shotlist, _make_profile(), str(tmp_path))
        args, _ = client.generate_image.call_args
        assert args[0] == expected_prompt


# ---------------------------------------------------------------------------
# render_assets: 폴백 이미지 테스트
# ---------------------------------------------------------------------------

class TestRenderAssetsFallback:
    def test_fallback_on_vendor_error(self, tmp_path):
        """VendorError 발생 시 폴백 이미지가 생성된다 (요구사항 10.6)."""
        client = _make_client(raises=VendorError("API 실패", vendor="Imagen"))
        shotlist = _make_shotlist(1)
        render_assets(client, shotlist, _make_profile(), str(tmp_path))
        path = Path(shotlist["shots"][0]["asset_path"])
        assert path.exists()

    def test_fallback_file_is_valid_image(self, tmp_path):
        """폴백 이미지 파일은 유효한 PNG여야 한다."""
        from PIL import Image  # 테스트 환경에는 PIL 있어야 함
        client = _make_client(raises=VendorError("API 실패", vendor="Imagen"))
        shotlist = _make_shotlist(1)
        render_assets(client, shotlist, _make_profile(), str(tmp_path))
        path = Path(shotlist["shots"][0]["asset_path"])
        img = Image.open(path)
        assert img.width == _FALLBACK_WIDTH
        assert img.height == _FALLBACK_HEIGHT

    def test_fallback_on_ratio_mismatch(self, tmp_path):
        """9:16이 아닌 이미지(예: 1:1)를 반환하면 폴백이 생성된다 (요구사항 10.5, 10.6)."""
        # 576×576 = 1:1 비율
        bad_bytes = _make_png_bytes(576, 576)
        client = _make_client(image_bytes=bad_bytes)
        shotlist = _make_shotlist(1)
        render_assets(client, shotlist, _make_profile(), str(tmp_path))
        path = Path(shotlist["shots"][0]["asset_path"])
        assert path.exists()
        from PIL import Image
        img = Image.open(path)
        # 폴백 이미지는 정확히 576×1024여야 한다
        assert img.width == _FALLBACK_WIDTH
        assert img.height == _FALLBACK_HEIGHT

    def test_run_continues_after_single_shot_failure(self, tmp_path):
        """한 숏이 실패해도 다른 숏들은 계속 처리된다 (요구사항 10.6)."""
        good_bytes = _make_png_bytes(576, 1024)

        def side_effect(prompt, *, aspect_ratio):
            if "shot 0" in prompt:
                raise VendorError("shot 0 실패", vendor="Imagen")
            return good_bytes

        client = MagicMock()
        client.generate_image.side_effect = side_effect
        client.synthesize_speech.return_value = b""

        shotlist = _make_shotlist(3)
        render_assets(client, shotlist, _make_profile(), str(tmp_path))

        for shot in shotlist["shots"]:
            assert shot["asset_path"] != ""
            assert Path(shot["asset_path"]).exists()

    def test_no_exception_raised_on_failure(self, tmp_path):
        """개별 실패 시 예외가 raise되지 않는다 (요구사항 10.6)."""
        client = _make_client(raises=VendorError("실패", vendor="Imagen"))
        shotlist = _make_shotlist(2)
        # 예외가 발생하면 테스트 실패
        result = render_assets(client, shotlist, _make_profile(), str(tmp_path))
        assert result is not None


# ---------------------------------------------------------------------------
# render_assets: 보이스오버 테스트
# ---------------------------------------------------------------------------

class TestRenderAssetsVoiceover:
    def test_voiceover_generated_when_enabled(self, tmp_path):
        """has_voiceover=True이면 voiceover.wav가 생성된다 (요구사항 10.3)."""
        client = _make_client()
        profile = _make_profile(has_voiceover=True)
        shotlist = _make_shotlist(1)
        render_assets(client, shotlist, profile, str(tmp_path))
        assert (tmp_path / "voiceover.wav").exists()

    def test_voiceover_not_generated_when_disabled(self, tmp_path):
        """has_voiceover=False이면 voiceover.wav가 생성되지 않는다."""
        client = _make_client()
        profile = _make_profile(has_voiceover=False)
        shotlist = _make_shotlist(1)
        render_assets(client, shotlist, profile, str(tmp_path))
        assert not (tmp_path / "voiceover.wav").exists()
        client.synthesize_speech.assert_not_called()

    def test_synthesize_speech_called_with_voice(self, tmp_path):
        """synthesize_speech는 voice 파라미터와 함께 호출된다."""
        client = _make_client()
        profile = _make_profile(has_voiceover=True)
        shotlist = _make_shotlist(1)
        render_assets(client, shotlist, profile, str(tmp_path), voice="ko-KR-Standard-B")
        _, kwargs = client.synthesize_speech.call_args
        assert kwargs["voice"] == "ko-KR-Standard-B"

    def test_default_voice_is_korean(self, tmp_path):
        """기본 목소리는 한국어 목소리다."""
        client = _make_client()
        profile = _make_profile(has_voiceover=True)
        shotlist = _make_shotlist(1)
        render_assets(client, shotlist, profile, str(tmp_path))
        _, kwargs = client.synthesize_speech.call_args
        assert "ko-KR" in kwargs["voice"]

    def test_voiceover_failure_does_not_stop_run(self, tmp_path):
        """보이스오버 생성 실패 시 run이 중단되지 않는다 (요구사항 10.6)."""
        client = _make_client()
        client.synthesize_speech.side_effect = VendorError("TTS 실패", vendor="TTS")
        profile = _make_profile(has_voiceover=True)
        shotlist = _make_shotlist(2)
        # 예외 없이 완료되어야 한다
        result = render_assets(client, shotlist, profile, str(tmp_path))
        assert result is not None
        # 이미지는 정상 생성됨
        for shot in shotlist["shots"]:
            assert Path(shot["asset_path"]).exists()


# ---------------------------------------------------------------------------
# _verify_ratio 단위 테스트
# ---------------------------------------------------------------------------

class TestVerifyRatio:
    def test_valid_9_16_ratio_passes(self):
        """576×1024 이미지 (9:16)는 검증을 통과한다."""
        img_bytes = _make_png_bytes(576, 1024)
        assert _verify_ratio(img_bytes) is True

    def test_valid_ratio_within_tolerance(self):
        """허용오차 내 비율은 통과한다 (요구사항 10.5)."""
        # 9:16 ≈ 0.5625; 540×960 = 0.5625 정확
        img_bytes = _make_png_bytes(540, 960)
        assert _verify_ratio(img_bytes) is True

    def test_square_ratio_fails(self):
        """1:1 비율은 검증을 통과하지 못한다."""
        img_bytes = _make_png_bytes(576, 576)
        assert _verify_ratio(img_bytes) is False

    def test_16_9_landscape_fails(self):
        """16:9 가로 비율은 검증을 통과하지 못한다."""
        img_bytes = _make_png_bytes(1024, 576)
        assert _verify_ratio(img_bytes) is False

    def test_ratio_constant_is_correct(self):
        """_TARGET_RATIO는 9/16이다."""
        assert abs(_TARGET_RATIO - 9 / 16) < 1e-9


# ---------------------------------------------------------------------------
# _generate_fallback_image 단위 테스트
# ---------------------------------------------------------------------------

class TestGenerateFallbackImage:
    def test_creates_file(self, tmp_path):
        """폴백 이미지 파일이 생성된다."""
        shot = {"index": 0, "role": "hook", "prompt": "Test prompt"}
        profile = _make_profile()
        out = tmp_path / "fallback.png"
        _generate_fallback_image(shot, profile, out)
        assert out.exists()

    def test_correct_dimensions(self, tmp_path):
        """폴백 이미지는 576×1024 (9:16)이다 (요구사항 13)."""
        from PIL import Image
        shot = {"index": 0, "role": "hook", "prompt": "some prompt"}
        profile = _make_profile()
        out = tmp_path / "fallback.png"
        _generate_fallback_image(shot, profile, out)
        img = Image.open(out)
        assert img.width == _FALLBACK_WIDTH
        assert img.height == _FALLBACK_HEIGHT

    def test_ratio_is_9_16(self, tmp_path):
        """폴백 이미지 비율은 9:16이다 (Property 13)."""
        shot = {"index": 0, "role": "hook", "prompt": "test"}
        profile = _make_profile()
        out = tmp_path / "fallback.png"
        _generate_fallback_image(shot, profile, out)
        img_bytes = out.read_bytes()
        assert _verify_ratio(img_bytes) is True

    def test_works_without_emoji_in_prompt(self, tmp_path):
        """이모지가 있는 프롬프트도 오류 없이 처리된다."""
        shot = {"index": 0, "role": "hook", "prompt": "테스트 ✨💖 프롬프트"}
        profile = _make_profile()
        out = tmp_path / "fallback.png"
        # 오류 없이 완료되어야 한다
        _generate_fallback_image(shot, profile, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# _build_vo_text 단위 테스트
# ---------------------------------------------------------------------------

class TestBuildVoText:
    def test_returns_string(self):
        """_build_vo_text는 항상 문자열을 반환한다."""
        profile = _make_profile()
        result = _build_vo_text(profile)
        assert isinstance(result, str)

    def test_returns_generic_when_no_slot_text(self):
        """슬롯에 text가 없으면 generic 문구를 반환한다."""
        profile = _make_profile()
        result = _build_vo_text(profile)
        assert len(result) > 0

    def test_uses_slot_text_when_present(self):
        """슬롯에 text가 있으면 그 텍스트를 사용한다."""
        profile = _make_profile()
        profile["captions"]["slots"][0]["text"] = "피부가 환해졌어요"
        result = _build_vo_text(profile)
        assert "피부가 환해졌어요" in result

    def test_empty_profile_returns_fallback(self):
        """profile에 captions가 없으면 generic 문구를 반환한다."""
        result = _build_vo_text({})
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _strip_emoji 단위 테스트
# ---------------------------------------------------------------------------

class TestStripEmoji:
    def test_removes_emoji(self):
        """이모지가 제거된다."""
        result = _strip_emoji("글로우 세럼 ✨")
        assert "✨" not in result

    def test_removes_multiple_emoji(self):
        """여러 이모지가 모두 제거된다."""
        result = _strip_emoji("💖 피부 ✨ 개선 🌟")
        assert "💖" not in result
        assert "✨" not in result
        assert "🌟" not in result

    def test_preserves_korean(self):
        """한국어는 보존된다."""
        result = _strip_emoji("피부가 달라졌어요 ✨")
        assert "피부가 달라졌어요" in result

    def test_preserves_alphanumeric(self):
        """알파벳·숫자는 보존된다."""
        result = _strip_emoji("abc 123 ✨")
        assert "abc 123" in result

    def test_empty_string(self):
        """빈 문자열은 빈 문자열을 반환한다."""
        assert _strip_emoji("") == ""


# ---------------------------------------------------------------------------
# _get_fallback_bg_color 단위 테스트
# ---------------------------------------------------------------------------

class TestGetFallbackBgColor:
    def test_returns_tuple_of_3_ints(self):
        """RGB 3-tuple을 반환한다."""
        color = _get_fallback_bg_color(_make_profile())
        assert isinstance(color, tuple)
        assert len(color) == 3
        assert all(isinstance(c, int) for c in color)

    def test_all_values_in_range(self):
        """모든 RGB 값은 0~255 범위 내에 있다."""
        color = _get_fallback_bg_color(_make_profile())
        assert all(0 <= c <= 255 for c in color)

    def test_uses_accent_color_when_present(self):
        """accent_color가 있으면 그 색상(밝게 조정)을 사용한다."""
        profile = _make_profile()
        profile["visual"]["accent_color"] = "#FF0000"  # 빨강
        color = _get_fallback_bg_color(profile)
        # 밝게 조정되므로 순수 빨강(255,0,0)이 아닌 분홍빛이어야 함
        assert color[0] > 200  # R 채널은 높아야 함

    def test_fallback_when_no_accent(self):
        """accent_color가 없으면 기본 파스텔 색상을 반환한다."""
        profile = _make_profile()
        profile["visual"].pop("accent_color", None)
        color = _get_fallback_bg_color(profile)
        assert isinstance(color, tuple)
        assert len(color) == 3


# ---------------------------------------------------------------------------
# 격리 불변식 테스트 (요구사항 13.2)
# ---------------------------------------------------------------------------

class TestIsolationInvariant:
    def test_assets_does_not_import_analyze(self):
        """assets.py 소스코드에 src.analyze import가 없다 (요구사항 13.2)."""
        import inspect
        import src.generate.assets as assets_module
        source = inspect.getsource(assets_module)
        # 소스 내에 src.analyze 또는 from src.analyze import 가 없어야 한다
        assert "src.analyze" not in source, (
            "assets.py 소스에 src.analyze import가 발견됨"
        )

    def test_assets_does_not_use_ref_paths(self):
        """assets.py 소스코드에 refs/ 경로가 없다 (요구사항 10.7, 13.3)."""
        import inspect
        import src.generate.assets as assets_module
        source = inspect.getsource(assets_module)
        assert "refs/" not in source

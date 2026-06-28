"""
tests/test_vendor_client.py — VendorClient 단위 테스트

재시도 정책, 지수 백오프, VendorError raise, image_to_video 스텁,
_parse_json_response, _silent_wav 등을 검증한다.
실제 Google API를 호출하지 않으며 모킹으로 결정적으로 검증한다.
"""

from __future__ import annotations

import json
import warnings
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.common.vendor_client import _retry, _silent_wav, VendorClient
from src.common.exceptions import VendorError


# ---------------------------------------------------------------------------
# 테스트용 VendorClient 팩토리
# ---------------------------------------------------------------------------
def _make_client() -> VendorClient:
    """테스트용 VendorClient를 반환한다. genai.Client는 모킹한다."""
    config = MagicMock()
    config.google_api_key = "test-key"
    config.gemini_model = "gemini-2.0-flash"
    config.imagen_model = "imagen-3.0-generate-002"
    config.veo_model = "veo-2.0"
    config.tts_voice = "ko-KR-Standard-A"
    with patch("src.common.vendor_client.genai.Client"):
        client = VendorClient(config)
    return client


# ---------------------------------------------------------------------------
# _silent_wav 테스트
# ---------------------------------------------------------------------------
class TestSilentWav:
    def test_returns_bytes(self):
        result = _silent_wav()
        assert isinstance(result, bytes)

    def test_starts_with_riff_header(self):
        result = _silent_wav()
        assert result[:4] == b"RIFF"
        assert result[8:12] == b"WAVE"

    def test_custom_duration_longer(self):
        wav1 = _silent_wav(duration_sec=1.0, sample_rate=22050)
        wav2 = _silent_wav(duration_sec=2.0, sample_rate=22050)
        assert len(wav2) > len(wav1)

    def test_data_chunk_present(self):
        result = _silent_wav(duration_sec=0.1)
        assert b"data" in result


# ---------------------------------------------------------------------------
# _retry 테스트
# ---------------------------------------------------------------------------
class TestRetry:
    def test_success_on_first_try(self):
        fn = MagicMock(return_value="ok")
        result = _retry("op", "Vendor", fn)
        assert result == "ok"
        assert fn.call_count == 1

    def test_success_on_second_try(self):
        fn = MagicMock(side_effect=[RuntimeError("fail"), "ok"])
        with patch("src.common.vendor_client.time.sleep"):
            result = _retry("op", "Vendor", fn)
        assert result == "ok"
        assert fn.call_count == 2

    def test_success_on_third_try(self):
        fn = MagicMock(side_effect=[RuntimeError("f1"), RuntimeError("f2"), "ok"])
        with patch("src.common.vendor_client.time.sleep"):
            result = _retry("op", "Vendor", fn)
        assert result == "ok"
        assert fn.call_count == 3

    def test_raises_vendor_error_after_all_retries(self):
        fn = MagicMock(side_effect=RuntimeError("persistent error"))
        with patch("src.common.vendor_client.time.sleep"):
            with pytest.raises(VendorError) as exc_info:
                _retry("analyze_video", "Gemini", fn)
        assert fn.call_count == 3
        err = exc_info.value
        assert err.vendor == "Gemini"
        assert err.operation == "analyze_video"

    def test_vendor_error_message_contains_operation(self):
        fn = MagicMock(side_effect=RuntimeError("err"))
        with patch("src.common.vendor_client.time.sleep"):
            with pytest.raises(VendorError) as exc_info:
                _retry("generate_text", "Gemini", fn)
        assert "generate_text" in str(exc_info.value)

    def test_backoff_sleep_called_between_retries(self):
        fn = MagicMock(side_effect=[RuntimeError("f1"), RuntimeError("f2"), "ok"])
        with patch("src.common.vendor_client.time.sleep") as mock_sleep:
            _retry("op", "Vendor", fn)
        # 2번 실패 → 2번 sleep (1s, 2s)
        assert mock_sleep.call_count == 2
        calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert calls == [1, 2]

    def test_no_sleep_on_first_success(self):
        fn = MagicMock(return_value="ok")
        with patch("src.common.vendor_client.time.sleep") as mock_sleep:
            _retry("op", "Vendor", fn)
        mock_sleep.assert_not_called()

    def test_all_three_retries_exhausted_sleep_pattern(self):
        """3회 모두 실패 시 sleep은 2번만 호출된다 (마지막엔 raise)."""
        fn = MagicMock(side_effect=RuntimeError("always fail"))
        with patch("src.common.vendor_client.time.sleep") as mock_sleep:
            with pytest.raises(VendorError):
                _retry("op", "Vendor", fn)
        assert mock_sleep.call_count == 2
        calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert calls == [1, 2]


# ---------------------------------------------------------------------------
# image_to_video 스텁 테스트
# ---------------------------------------------------------------------------
class TestVendorClientImageToVideo:
    def test_returns_empty_bytes(self):
        client = _make_client()
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = client.image_to_video(b"fake_image", "prompt", duration_sec=3.0)
        assert result == b""

    def test_emits_warning(self):
        client = _make_client()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            client.image_to_video(b"fake", "prompt", duration_sec=5.0)
        assert len(w) >= 1
        assert "Veo not yet implemented" in str(w[0].message)


# ---------------------------------------------------------------------------
# _parse_json_response 테스트
# ---------------------------------------------------------------------------
class TestVendorClientParseJsonResponse:
    def test_plain_json(self):
        client = _make_client()
        data = {"key": "value", "num": 42}
        result = client._parse_json_response(json.dumps(data))
        assert result == data

    def test_fenced_json(self):
        client = _make_client()
        data = {"verdict": "pass", "reasons": ["good"]}
        fenced = f"```json\n{json.dumps(data)}\n```"
        result = client._parse_json_response(fenced)
        assert result == data

    def test_fenced_without_lang(self):
        client = _make_client()
        data = {"a": 1}
        fenced = f"```\n{json.dumps(data)}\n```"
        result = client._parse_json_response(fenced)
        assert result == data

    def test_whitespace_stripped(self):
        client = _make_client()
        data = {"x": "y"}
        result = client._parse_json_response("  " + json.dumps(data) + "  ")
        assert result == data

    def test_invalid_json_raises(self):
        client = _make_client()
        with pytest.raises(json.JSONDecodeError):
            client._parse_json_response("not json at all")


# ---------------------------------------------------------------------------
# generate_text 테스트
# ---------------------------------------------------------------------------
class TestVendorClientGenerateText:
    def test_returns_stripped_text(self):
        client = _make_client()

        mock_response = MagicMock()
        mock_response.text = "  생성된 훅 텍스트  "
        client._client.models.generate_content.return_value = mock_response

        result = client.generate_text("프롬프트", temperature=0.9, seed=42)
        assert result == "생성된 훅 텍스트"

    def test_calls_generate_content(self):
        client = _make_client()

        mock_response = MagicMock()
        mock_response.text = "훅"
        client._client.models.generate_content.return_value = mock_response

        client.generate_text("프롬프트", temperature=0.8, seed=None)
        client._client.models.generate_content.assert_called_once()

    def test_retries_on_failure(self):
        client = _make_client()

        mock_response = MagicMock()
        mock_response.text = "ok"
        client._client.models.generate_content.side_effect = [
            RuntimeError("fail"),
            mock_response,
        ]

        with patch("src.common.vendor_client.time.sleep"):
            result = client.generate_text("p", temperature=0.9, seed=1)

        assert result == "ok"
        assert client._client.models.generate_content.call_count == 2

    def test_raises_vendor_error_after_exhaustion(self):
        client = _make_client()
        client._client.models.generate_content.side_effect = RuntimeError("quota")

        with patch("src.common.vendor_client.time.sleep"):
            with pytest.raises(VendorError) as exc_info:
                client.generate_text("p", temperature=0.9, seed=None)

        assert exc_info.value.operation == "generate_text"


# ---------------------------------------------------------------------------
# analyze_video 테스트
# ---------------------------------------------------------------------------
class TestVendorClientAnalyzeVideo:
    def test_success_returns_dict(self):
        client = _make_client()

        mock_file = MagicMock()
        client._client.files.upload.return_value = mock_file

        mock_response = MagicMock()
        mock_response.text = '{"narrative": {"beats": []}}'
        client._client.models.generate_content.return_value = mock_response

        result = client.analyze_video("/fake/video.mp4", "프롬프트")
        assert isinstance(result, dict)
        assert "narrative" in result

    def test_retries_on_upload_failure_then_raises(self):
        client = _make_client()
        client._client.files.upload.side_effect = RuntimeError("upload failed")

        with patch("src.common.vendor_client.time.sleep"):
            with pytest.raises(VendorError) as exc_info:
                client.analyze_video("/fake/video.mp4", "프롬프트")

        assert exc_info.value.operation == "analyze_video"
        assert exc_info.value.vendor == "Gemini"


# ---------------------------------------------------------------------------
# judge_video 테스트
# ---------------------------------------------------------------------------
class TestVendorClientJudgeVideo:
    def test_returns_verdict_and_reasons(self):
        client = _make_client()

        client._client.files.upload.return_value = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"verdict": "PASS", "reasons": ["good pacing"]}'
        client._client.models.generate_content.return_value = mock_response

        result = client.judge_video("/fake/final.mp4", "판정 기준")
        assert result["verdict"] == "PASS"
        assert isinstance(result["reasons"], list)

    def test_normalizes_non_list_reasons(self):
        client = _make_client()

        client._client.files.upload.return_value = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"verdict": "FAIL", "reasons": "단일 이유"}'
        client._client.models.generate_content.return_value = mock_response

        result = client.judge_video("/fake/final.mp4", "기준")
        assert isinstance(result["reasons"], list)
        assert result["reasons"] == ["단일 이유"]

    def test_missing_keys_default_to_empty(self):
        client = _make_client()

        client._client.files.upload.return_value = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{}'
        client._client.models.generate_content.return_value = mock_response

        result = client.judge_video("/fake/final.mp4", "기준")
        assert result["verdict"] == ""
        assert result["reasons"] == []


# ---------------------------------------------------------------------------
# generate_image 테스트
# ---------------------------------------------------------------------------
class TestVendorClientGenerateImage:
    def test_returns_image_bytes(self):
        client = _make_client()

        mock_image = MagicMock()
        mock_image.image_bytes = b"\x89PNG\r\n\x1a\n"
        mock_generated = MagicMock()
        mock_generated.image = mock_image
        mock_result = MagicMock()
        mock_result.generated_images = [mock_generated]
        client._client.models.generate_images.return_value = mock_result

        result = client.generate_image("뷰티 제품 이미지", aspect_ratio="9:16")
        assert result == b"\x89PNG\r\n\x1a\n"

    def test_calls_with_correct_params(self):
        client = _make_client()

        mock_image = MagicMock()
        mock_image.image_bytes = b"bytes"
        mock_generated = MagicMock()
        mock_generated.image = mock_image
        mock_result = MagicMock()
        mock_result.generated_images = [mock_generated]
        client._client.models.generate_images.return_value = mock_result

        client.generate_image("테스트 프롬프트", aspect_ratio="9:16")
        client._client.models.generate_images.assert_called_once()
        call_kwargs = client._client.models.generate_images.call_args
        assert call_kwargs.kwargs["model"] == client._config.imagen_model
        assert call_kwargs.kwargs["prompt"] == "테스트 프롬프트"

    def test_raises_vendor_error_on_failure(self):
        client = _make_client()
        client._client.models.generate_images.side_effect = RuntimeError("quota exceeded")

        with patch("src.common.vendor_client.time.sleep"):
            with pytest.raises(VendorError) as exc_info:
                client.generate_image("프롬프트", aspect_ratio="9:16")

        assert exc_info.value.operation == "generate_image"
        assert exc_info.value.vendor == "Imagen"


# ---------------------------------------------------------------------------
# synthesize_speech 폴백 테스트
# ---------------------------------------------------------------------------
class TestVendorClientSynthesizeSpeech:
    def test_fallback_when_cloud_tts_not_installed(self):
        """google-cloud-texttospeech가 없으면 무음 WAV를 반환한다."""
        client = _make_client()

        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "google.cloud.texttospeech":
                raise ImportError("No module named 'google.cloud.texttospeech'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                result = client.synthesize_speech("안녕하세요", voice="ko-KR-Standard-A")

        assert isinstance(result, bytes)
        # 무음 WAV 헤더 확인
        assert result[:4] == b"RIFF"
        assert result[8:12] == b"WAVE"

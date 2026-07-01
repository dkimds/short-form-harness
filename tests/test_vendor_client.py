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
# image_to_video 테스트 (Gemini Omni Flash 기반 image-to-video)
# ---------------------------------------------------------------------------
class TestVendorClientImageToVideo:
    @pytest.fixture(autouse=True)
    def _isolate_veo_call_timer(self):
        """_last_veo_call_time 전역 상태를 테스트마다 초기화한다.

        image_to_video()는 호출 간 최소 간격(_VEO_MIN_INTERVAL_SECONDS)을
        모듈 전역 시각으로 추적하는데, 이 상태가 테스트 간에 누적되면
        이전 테스트의 호출 시각이 남아 다음 테스트에서 실제로 대기가
        발생할 수 있다. 매 테스트 전에 충분히 과거로 리셋해 대기가
        걸리지 않게 한다.
        """
        import time as _time
        import src.common.vendor_client as _vc
        _vc._last_veo_call_time = _time.monotonic() - 3600
        yield

    def _make_omni_interaction(self, video_bytes: bytes | None = b"mp4data") -> MagicMock:
        """모킹된 interactions.create() 응답을 반환한다."""
        import base64
        interaction = MagicMock()
        if video_bytes is None:
            interaction.output_video = None
        else:
            interaction.output_video.data = base64.b64encode(video_bytes).decode("utf-8")
        return interaction

    def test_returns_video_bytes_on_success(self):
        """interactions.create가 성공하면 디코딩된 비디오 바이트를 반환한다."""
        client = _make_client()
        client._client.interactions.create.return_value = self._make_omni_interaction(
            video_bytes=b"fake_mp4_bytes"
        )

        with patch("src.common.vendor_client.time.sleep"):
            result = client.image_to_video(b"png_bytes", "test prompt", duration_sec=3.0)
        assert result == b"fake_mp4_bytes"

    def test_calls_interactions_create_with_correct_params(self):
        """interactions.create는 올바른 파라미터로 호출된다."""
        client = _make_client()
        client._client.interactions.create.return_value = self._make_omni_interaction()

        with patch("src.common.vendor_client.time.sleep"):
            client.image_to_video(b"img", "my prompt", duration_sec=5.0)

        call_kwargs = client._client.interactions.create.call_args.kwargs
        assert call_kwargs["model"] == client._config.veo_model
        input_parts = call_kwargs["input"]
        assert input_parts[0]["type"] == "image"
        assert input_parts[0]["mime_type"] == "image/png"
        assert "my prompt" in input_parts[1]["text"]
        assert call_kwargs["generation_config"]["video_config"]["task"] == "image_to_video"
        assert call_kwargs["response_format"]["aspect_ratio"] == "9:16"

    def test_raises_vendor_error_on_api_failure(self):
        """API 호출 실패 시 VendorError가 raise된다."""
        client = _make_client()
        client._client.interactions.create.side_effect = RuntimeError("quota exceeded")

        with patch("src.common.vendor_client.time.sleep"):
            with pytest.raises(VendorError) as exc_info:
                client.image_to_video(b"img", "prompt", duration_sec=3.0)

        assert exc_info.value.vendor == "Veo"
        assert exc_info.value.operation == "image_to_video"

    def test_raises_vendor_error_on_missing_output_video(self):
        """응답에 output_video가 없으면 VendorError가 raise된다."""
        client = _make_client()
        client._client.interactions.create.return_value = self._make_omni_interaction(
            video_bytes=None
        )

        with patch("src.common.vendor_client.time.sleep"):
            with pytest.raises(VendorError):
                client.image_to_video(b"img", "prompt", duration_sec=3.0)

    def test_retries_use_veo_specific_backoff(self):
        """호출 실패 시 일반 백오프(1,2,4s)가 아니라 전용 백오프(30,60s)를 쓴다."""
        client = _make_client()
        client._client.interactions.create.side_effect = RuntimeError("429 quota")

        with patch("src.common.vendor_client.time.sleep") as mock_sleep:
            with pytest.raises(VendorError):
                client.image_to_video(b"img", "prompt", duration_sec=3.0)

        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list if c.args]
        assert any(s >= 30 for s in sleep_calls), f"백오프가 30s 이상이어야 함: {sleep_calls}"

    def test_enforces_minimum_interval_between_calls(self):
        """연속 image_to_video 호출 사이에 최소 간격을 둔다."""
        import time as _time
        import src.common.vendor_client as _vc

        client = _make_client()
        client._client.interactions.create.return_value = self._make_omni_interaction()

        # 방금 호출한 것처럼 설정 — 다음 호출은 최소 간격만큼 대기해야 함
        _vc._last_veo_call_time = _time.monotonic()

        with patch("src.common.vendor_client.time.sleep") as mock_sleep:
            client.image_to_video(b"img", "prompt", duration_sec=3.0)

        # 첫 sleep 호출이 최소 간격 확보용이어야 함 (약 20초 이하, 0보다 커야 함)
        assert mock_sleep.call_args_list, "최소 간격 대기를 위한 sleep이 호출되어야 함"
        first_wait = mock_sleep.call_args_list[0].args[0]
        assert 0 < first_wait <= _vc._VEO_MIN_INTERVAL_SECONDS


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

        # Files API 폴링: state.name == "ACTIVE" 를 즉시 반환하도록 설정
        mock_file_info = MagicMock()
        mock_file_info.state.name = "ACTIVE"
        client._client.files.get.return_value = mock_file_info

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
    def _mock_active_file(self, client) -> None:
        """Files API 폴링: state.name == "ACTIVE" 를 즉시 반환하도록 설정."""
        client._client.files.upload.return_value = MagicMock()
        mock_file_info = MagicMock()
        mock_file_info.state.name = "ACTIVE"
        client._client.files.get.return_value = mock_file_info

    def test_returns_verdict_and_reasons(self):
        client = _make_client()

        self._mock_active_file(client)
        mock_response = MagicMock()
        mock_response.text = '{"verdict": "PASS", "reasons": ["good pacing"]}'
        client._client.models.generate_content.return_value = mock_response

        result = client.judge_video("/fake/final.mp4", "판정 기준")
        assert result["verdict"] == "PASS"
        assert isinstance(result["reasons"], list)

    def test_normalizes_non_list_reasons(self):
        client = _make_client()

        self._mock_active_file(client)
        mock_response = MagicMock()
        mock_response.text = '{"verdict": "FAIL", "reasons": "단일 이유"}'
        client._client.models.generate_content.return_value = mock_response

        result = client.judge_video("/fake/final.mp4", "기준")
        assert isinstance(result["reasons"], list)
        assert result["reasons"] == ["단일 이유"]

    def test_missing_keys_default_to_empty(self):
        client = _make_client()

        self._mock_active_file(client)
        mock_response = MagicMock()
        mock_response.text = '{}'
        client._client.models.generate_content.return_value = mock_response

        result = client.judge_video("/fake/final.mp4", "기준")
        assert result["verdict"] == ""
        assert result["reasons"] == []


# ---------------------------------------------------------------------------
# generate_image 테스트
# ---------------------------------------------------------------------------
def _mock_image_response(image_bytes: bytes) -> MagicMock:
    """generate_content가 이미지 파트를 담아 반환하는 응답을 모킹한다."""
    part = MagicMock()
    part.inline_data.data = image_bytes
    response = MagicMock()
    response.parts = [part]
    return response


class TestVendorClientGenerateImage:
    def test_returns_image_bytes(self):
        client = _make_client()
        client._client.models.generate_content.return_value = _mock_image_response(
            b"\x89PNG\r\n\x1a\n"
        )

        result = client.generate_image("뷰티 제품 이미지", aspect_ratio="9:16")
        assert result == b"\x89PNG\r\n\x1a\n"

    def test_calls_with_correct_params(self):
        client = _make_client()
        client._client.models.generate_content.return_value = _mock_image_response(b"bytes")

        client.generate_image("테스트 프롬프트", aspect_ratio="9:16")
        client._client.models.generate_content.assert_called_once()
        call_kwargs = client._client.models.generate_content.call_args
        assert call_kwargs.kwargs["model"] == client._config.imagen_model
        assert call_kwargs.kwargs["contents"] == ["테스트 프롬프트"]

    def test_raises_vendor_error_on_failure(self):
        client = _make_client()
        client._client.models.generate_content.side_effect = RuntimeError("quota exceeded")

        with patch("src.common.vendor_client.time.sleep"):
            with pytest.raises(VendorError) as exc_info:
                client.generate_image("프롬프트", aspect_ratio="9:16")

        assert exc_info.value.operation == "generate_image"
        assert exc_info.value.vendor == "Gemini"

    def test_raises_vendor_error_when_no_image_part(self):
        """응답에 이미지 파트가 없으면 VendorError를 raise한다."""
        client = _make_client()
        empty_response = MagicMock()
        empty_response.parts = []
        client._client.models.generate_content.return_value = empty_response

        with patch("src.common.vendor_client.time.sleep"):
            with pytest.raises(VendorError):
                client.generate_image("프롬프트", aspect_ratio="9:16")

    def test_reference_image_included_when_provided(self):
        """reference_image가 주어지면 contents에 이미지 파트가 함께 포함된다."""
        client = _make_client()
        client._client.models.generate_content.return_value = _mock_image_response(b"bytes")

        client.generate_image(
            "테스트 프롬프트", aspect_ratio="9:16", reference_image=b"\xff\xd8\xff"
        )
        call_kwargs = client._client.models.generate_content.call_args
        contents = call_kwargs.kwargs["contents"]
        assert len(contents) == 2
        assert "테스트 프롬프트" in contents[0]

    def test_reference_image_prompt_includes_consistency_instruction(self):
        """reference_image가 있으면 프롬프트에 인물 일관성 유지 지시문이 덧붙는다.

        이미지 파트만 넘기고 지시문이 없으면 모델이 참조 이미지를 "스타일
        참고" 정도로만 취급해 인물이 매 호출마다 달라지는 문제가 있었다.
        """
        client = _make_client()
        client._client.models.generate_content.return_value = _mock_image_response(b"bytes")

        client.generate_image(
            "테스트 프롬프트", aspect_ratio="9:16", reference_image=b"\xff\xd8\xff"
        )
        call_kwargs = client._client.models.generate_content.call_args
        prompt_text = call_kwargs.kwargs["contents"][0]
        assert "reference image" in prompt_text.lower()
        assert "consistent" in prompt_text.lower()

    def test_reference_image_omitted_when_none(self):
        """reference_image가 None이면 contents에는 프롬프트 텍스트만 포함되고 지시문도 없다."""
        client = _make_client()
        client._client.models.generate_content.return_value = _mock_image_response(b"bytes")

        client.generate_image("테스트 프롬프트", aspect_ratio="9:16", reference_image=None)
        call_kwargs = client._client.models.generate_content.call_args
        assert call_kwargs.kwargs["contents"] == ["테스트 프롬프트"]


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

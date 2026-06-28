"""
tests/test_vision.py — analyze_vision 단위 테스트

VendorClient를 모킹해 외부 API 없이 결정적으로 검증한다.
검증 대상:
  - 정상 호출 → 구조화 dict 반환
  - VendorError → None 반환
  - 프롬프트 파일 부재 → None 반환
  - 응답 섹션 일부 누락 → 경고 로그 + 부분 dict 반환
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.analyze.vision import analyze_vision, _PROMPT_PATH
from src.common.exceptions import VendorError


# ---------------------------------------------------------------------------
# 공통 픽스처 및 헬퍼
# ---------------------------------------------------------------------------

def _make_client(response: dict | None = None, *, raise_error: bool = False) -> MagicMock:
    """analyze_video 메서드가 모킹된 VendorClient를 반환한다."""
    client = MagicMock()
    if raise_error:
        client.analyze_video.side_effect = VendorError(
            "Gemini API 호출 실패",
            vendor="Gemini",
            operation="analyze_video",
        )
    else:
        client.analyze_video.return_value = response or {}
    return client


_FULL_RESPONSE: dict = {
    "narrative": {
        "beats": [
            {
                "role": "hook",
                "start_sec": 0.0,
                "end_sec": 2.8,
                "shot_type": "extreme_closeup_handheld_selfie",
                "intent": "Grab attention immediately",
            }
        ]
    },
    "captions": {
        "slots": [
            {
                "name": "title_hook",
                "anchor": "top_center",
                "font_style": "white_semibold_soft_shadow",
                "size_pct": 6.5,
                "appear_sec": 0.3,
                "duration_sec": 3.0,
                "emoji_palette": ["✨"],
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
        "vo_style": None,
    },
}


# ---------------------------------------------------------------------------
# 정상 경로 — 완전한 응답
# ---------------------------------------------------------------------------

class TestAnalyzeVisionSuccess:
    def test_returns_dict_on_success(self):
        """정상 호출 시 dict를 반환한다."""
        client = _make_client(_FULL_RESPONSE)
        result = analyze_vision(client, "/fake/video.mp4")
        assert isinstance(result, dict)

    def test_narrative_section_present(self):
        """반환된 dict에 narrative 섹션이 포함된다."""
        client = _make_client(_FULL_RESPONSE)
        result = analyze_vision(client, "/fake/video.mp4")
        assert "narrative" in result
        assert "beats" in result["narrative"]

    def test_captions_section_present(self):
        """반환된 dict에 captions 섹션이 포함된다."""
        client = _make_client(_FULL_RESPONSE)
        result = analyze_vision(client, "/fake/video.mp4")
        assert "captions" in result
        assert "slots" in result["captions"]

    def test_visual_section_present(self):
        """반환된 dict에 visual 섹션이 포함된다."""
        client = _make_client(_FULL_RESPONSE)
        result = analyze_vision(client, "/fake/video.mp4")
        assert "visual" in result
        assert "color_grade" in result["visual"]

    def test_audio_section_present_with_required_fields(self):
        """반환된 dict에 audio 섹션과 music_mood·vo_style 필드가 포함된다."""
        client = _make_client(_FULL_RESPONSE)
        result = analyze_vision(client, "/fake/video.mp4")
        assert "audio" in result
        assert "music_mood" in result["audio"]
        assert "vo_style" in result["audio"]

    def test_passes_prompt_to_analyze_video(self):
        """VendorClient.analyze_video에 프롬프트 텍스트가 전달된다."""
        client = _make_client(_FULL_RESPONSE)
        analyze_vision(client, "/fake/video.mp4")
        client.analyze_video.assert_called_once()
        call_args = client.analyze_video.call_args
        path_arg, prompt_arg = call_args.args
        assert path_arg == "/fake/video.mp4"
        assert isinstance(prompt_arg, str)
        assert len(prompt_arg) > 0

    def test_passes_correct_video_path(self):
        """VendorClient.analyze_video에 올바른 경로가 전달된다."""
        client = _make_client(_FULL_RESPONSE)
        test_path = "/some/path/reference.mp4"
        analyze_vision(client, test_path)
        call_args = client.analyze_video.call_args
        assert call_args.args[0] == test_path


# ---------------------------------------------------------------------------
# VendorError 처리
# ---------------------------------------------------------------------------

class TestAnalyzeVisionVendorError:
    def test_returns_none_on_vendor_error(self):
        """VendorError 발생 시 None을 반환한다 (요구사항 5.6)."""
        client = _make_client(raise_error=True)
        result = analyze_vision(client, "/fake/video.mp4")
        assert result is None

    def test_logs_error_on_vendor_error(self, caplog):
        """VendorError 발생 시 오류를 로그에 기록한다."""
        import logging
        client = _make_client(raise_error=True)
        with caplog.at_level(logging.ERROR, logger="src.analyze.vision"):
            analyze_vision(client, "/fake/video.mp4")
        assert any("비전 분석 실패" in msg or "재시도" in msg for msg in caplog.messages)

    def test_does_not_reraise_vendor_error(self):
        """VendorError가 호출부로 전파되지 않는다."""
        client = _make_client(raise_error=True)
        # 예외가 발생하지 않아야 함
        result = analyze_vision(client, "/fake/video.mp4")
        assert result is None


# ---------------------------------------------------------------------------
# 프롬프트 파일 부재
# ---------------------------------------------------------------------------

class TestAnalyzeVisionPromptNotFound:
    def test_returns_none_when_prompt_file_missing(self):
        """프롬프트 파일이 없으면 None을 반환한다."""
        client = _make_client(_FULL_RESPONSE)
        with patch("src.analyze.vision._PROMPT_PATH") as mock_path:
            mock_path.read_text.side_effect = FileNotFoundError("no file")
            result = analyze_vision(client, "/fake/video.mp4")
        assert result is None

    def test_vendor_not_called_when_prompt_missing(self):
        """프롬프트 파일이 없으면 VendorClient를 호출하지 않는다."""
        client = _make_client(_FULL_RESPONSE)
        with patch("src.analyze.vision._PROMPT_PATH") as mock_path:
            mock_path.read_text.side_effect = FileNotFoundError("no file")
            analyze_vision(client, "/fake/video.mp4")
        client.analyze_video.assert_not_called()

    def test_logs_error_when_prompt_missing(self, caplog):
        """프롬프트 파일이 없으면 오류를 로그에 기록한다."""
        import logging
        client = _make_client(_FULL_RESPONSE)
        with patch("src.analyze.vision._PROMPT_PATH") as mock_path:
            mock_path.read_text.side_effect = FileNotFoundError("no file")
            with caplog.at_level(logging.ERROR, logger="src.analyze.vision"):
                analyze_vision(client, "/fake/video.mp4")
        assert len(caplog.messages) > 0


# ---------------------------------------------------------------------------
# 누락된 섹션 — 부분 결과 반환
# ---------------------------------------------------------------------------

class TestAnalyzeVisionMissingSections:
    def test_returns_partial_dict_when_narrative_missing(self):
        """narrative 섹션 누락 시에도 파싱된 부분 dict를 반환한다."""
        partial = {k: v for k, v in _FULL_RESPONSE.items() if k != "narrative"}
        client = _make_client(partial)
        result = analyze_vision(client, "/fake/video.mp4")
        assert result is not None
        assert isinstance(result, dict)
        assert "captions" in result
        assert "visual" in result
        assert "audio" in result

    def test_returns_partial_dict_when_visual_missing(self):
        """visual 섹션 누락 시에도 부분 dict를 반환한다."""
        partial = {k: v for k, v in _FULL_RESPONSE.items() if k != "visual"}
        client = _make_client(partial)
        result = analyze_vision(client, "/fake/video.mp4")
        assert result is not None
        assert "narrative" in result

    def test_logs_warning_when_section_missing(self, caplog):
        """필수 섹션 누락 시 경고를 로그에 기록한다."""
        import logging
        partial = {k: v for k, v in _FULL_RESPONSE.items() if k != "audio"}
        client = _make_client(partial)
        with caplog.at_level(logging.WARNING, logger="src.analyze.vision"):
            analyze_vision(client, "/fake/video.mp4")
        assert any("누락" in msg or "missing" in msg.lower() for msg in caplog.messages)

    def test_logs_warning_when_audio_fields_missing(self, caplog):
        """audio 섹션에 music_mood / vo_style 누락 시 경고를 기록한다."""
        import logging
        partial = dict(_FULL_RESPONSE)
        partial["audio"] = {"unknown_field": "value"}  # music_mood, vo_style 없음
        client = _make_client(partial)
        with caplog.at_level(logging.WARNING, logger="src.analyze.vision"):
            analyze_vision(client, "/fake/video.mp4")
        assert any(
            "music_mood" in msg or "vo_style" in msg or "audio" in msg
            for msg in caplog.messages
        )

    def test_returns_none_is_not_triggered_by_missing_section(self):
        """섹션 누락만으로는 None이 반환되지 않는다 (부분 결과 허용)."""
        # 모든 섹션 누락 — 빈 dict이어도 None이 아닌 dict 반환
        client = _make_client({})
        result = analyze_vision(client, "/fake/video.mp4")
        assert result is not None
        assert result == {}

    def test_all_sections_present_no_warning(self, caplog):
        """모든 섹션이 있으면 경고 로그가 발생하지 않는다."""
        import logging
        client = _make_client(_FULL_RESPONSE)
        with caplog.at_level(logging.WARNING, logger="src.analyze.vision"):
            analyze_vision(client, "/fake/video.mp4")
        assert caplog.messages == []


# ---------------------------------------------------------------------------
# 프롬프트 파일 실제 존재 확인 (통합 수준)
# ---------------------------------------------------------------------------

class TestPromptFileExists:
    def test_prompt_file_is_on_disk(self):
        """prompts/analyze_vision.md 파일이 실제로 존재한다."""
        assert _PROMPT_PATH.exists(), (
            f"프롬프트 파일이 없습니다: {_PROMPT_PATH}\n"
            "  prompts/analyze_vision.md 파일을 생성하세요."
        )

    def test_prompt_file_is_non_empty(self):
        """prompts/analyze_vision.md 파일이 비어 있지 않다."""
        if _PROMPT_PATH.exists():
            content = _PROMPT_PATH.read_text(encoding="utf-8")
            assert len(content.strip()) > 0

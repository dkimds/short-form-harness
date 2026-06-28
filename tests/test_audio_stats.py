"""
tests/test_audio_stats.py — AudioStats 단위 테스트

subprocess(ffprobe/ffmpeg)와 librosa 내부 헬퍼를 모킹해 순수 로직을 검증한다.
librosa/numpy가 환경에 없을 수 있으므로, librosa를 사용하는 내부 함수
(_detect_music_start, _detect_voiceover)를 직접 패치한다.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.analyze.audio_stats import AudioStats, analyze_audio


# ---------------------------------------------------------------------------
# 헬퍼: ffprobe 응답 빌더
# ---------------------------------------------------------------------------

def _ffprobe_response(has_audio: bool) -> str:
    """ffprobe JSON 응답 문자열을 만든다."""
    if has_audio:
        streams = [{"codec_type": "video"}, {"codec_type": "audio"}]
    else:
        streams = [{"codec_type": "video"}]
    return json.dumps({"streams": streams})


def _loudnorm_stderr(input_i: float = -14.5) -> str:
    """ffmpeg loudnorm stderr 출력 문자열을 만든다."""
    return (
        "Some ffmpeg output\n"
        + json.dumps(
            {
                "input_i": str(input_i),
                "input_tp": "-1.0",
                "input_lra": "5.0",
                "input_thresh": "-24.5",
                "output_i": str(input_i),
                "output_tp": "-1.0",
                "output_lra": "5.0",
                "output_thresh": "-24.5",
                "normalization_type": "dynamic",
                "target_offset": "0.0",
            }
        )
    )


def _make_subprocess_side_effect(
    lufs: float = -14.5,
    wav_bytes: bytes = b"RIFF" + b"\x00" * 44,
):
    """subprocess.run 호출 순서에 맞는 side_effect 함수를 반환한다.

    호출 순서:
      1. ffprobe  (오디오 스트림 확인)
      2. ffmpeg loudnorm  (LUFS 측정)
      3. ffmpeg wav 추출  (음악 시작 + VO 감지 공용)
    """
    def side_effect(cmd, **kwargs):
        mock = MagicMock()
        if "ffprobe" in cmd[0]:
            mock.stdout = _ffprobe_response(has_audio=True)
            mock.returncode = 0
        elif "ffmpeg" in cmd[0] and "loudnorm" in " ".join(cmd):
            mock.stderr = _loudnorm_stderr(lufs)
            mock.stdout = ""
            mock.returncode = 0
        else:
            # WAV 추출
            mock.stdout = wav_bytes
            mock.returncode = 0
        return mock

    return side_effect


# ---------------------------------------------------------------------------
# 테스트: 오디오 스트림 없음 → 기본값 반환
# ---------------------------------------------------------------------------

class TestNoAudioStream:
    """오디오 스트림이 없는 파일 처리."""

    def test_no_audio_returns_default_audiostats(self):
        """오디오 트랙이 없으면 AudioStats(0.0, -23.0, False)를 반환해야 한다."""
        ffprobe_result = MagicMock()
        ffprobe_result.stdout = _ffprobe_response(has_audio=False)
        ffprobe_result.returncode = 0

        with patch("subprocess.run", return_value=ffprobe_result):
            result = analyze_audio("no_audio.mp4")

        assert result == AudioStats(music_start_sec=0.0, target_lufs=-23.0, has_voiceover=False)

    def test_no_audio_has_voiceover_false(self):
        """오디오 부재 시 has_voiceover는 반드시 False여야 한다 (요구사항 4.4)."""
        ffprobe_result = MagicMock()
        ffprobe_result.stdout = _ffprobe_response(has_audio=False)

        with patch("subprocess.run", return_value=ffprobe_result):
            result = analyze_audio("silent.mp4")

        assert result.has_voiceover is False

    def test_ffprobe_json_parse_failure_treated_as_no_audio(self):
        """ffprobe 출력이 JSON이 아니면 오디오 없음으로 처리한다."""
        ffprobe_result = MagicMock()
        ffprobe_result.stdout = "not valid json"

        with patch("subprocess.run", return_value=ffprobe_result):
            result = analyze_audio("broken.mp4")

        assert result == AudioStats(music_start_sec=0.0, target_lufs=-23.0, has_voiceover=False)


# ---------------------------------------------------------------------------
# 테스트: 유효 오디오 → plausible AudioStats
# librosa 내부 헬퍼(_detect_music_start, _detect_voiceover)를 직접 패치해
# librosa/numpy 설치 없이도 동작하게 한다.
# ---------------------------------------------------------------------------

_MODULE = "src.analyze.audio_stats"


class TestValidAudio:
    """오디오 스트림이 있는 파일 처리."""

    def test_valid_audio_returns_audiostats_with_values(self):
        """유효 오디오 파일은 AudioStats를 반환하고 필드가 채워져 있어야 한다."""
        with patch("subprocess.run", side_effect=_make_subprocess_side_effect(lufs=-14.5)):
            with patch(f"{_MODULE}._detect_music_start", return_value=0.5):
                with patch(f"{_MODULE}._detect_voiceover", return_value=False):
                    result = analyze_audio("valid.mp4")

        assert isinstance(result, AudioStats)
        assert result.target_lufs == pytest.approx(-14.5)
        assert result.music_start_sec == pytest.approx(0.5)

    def test_lufs_is_measured(self):
        """target_lufs는 loudnorm 측정값이어야 한다 (요구사항 4.2)."""
        with patch("subprocess.run", side_effect=_make_subprocess_side_effect(lufs=-18.3)):
            with patch(f"{_MODULE}._detect_music_start", return_value=0.0):
                with patch(f"{_MODULE}._detect_voiceover", return_value=False):
                    result = analyze_audio("audio.mp4")

        assert result.target_lufs == pytest.approx(-18.3)

    def test_no_onset_gives_music_start_zero(self):
        """onset이 감지되지 않으면 music_start_sec는 0.0이어야 한다."""
        with patch("subprocess.run", side_effect=_make_subprocess_side_effect()):
            with patch(f"{_MODULE}._detect_music_start", return_value=0.0):
                with patch(f"{_MODULE}._detect_voiceover", return_value=False):
                    result = analyze_audio("nobeat.mp4")

        assert result.music_start_sec == pytest.approx(0.0)

    def test_has_voiceover_true_when_speech_detected(self):
        """VO 헬퍼가 True를 반환하면 has_voiceover=True여야 한다 (요구사항 4.3)."""
        with patch("subprocess.run", side_effect=_make_subprocess_side_effect()):
            with patch(f"{_MODULE}._detect_music_start", return_value=0.0):
                with patch(f"{_MODULE}._detect_voiceover", return_value=True):
                    result = analyze_audio("vo.mp4")

        assert result.has_voiceover is True

    def test_has_voiceover_false_when_no_speech(self):
        """VO 헬퍼가 False를 반환하면 has_voiceover=False여야 한다."""
        with patch("subprocess.run", side_effect=_make_subprocess_side_effect()):
            with patch(f"{_MODULE}._detect_music_start", return_value=0.0):
                with patch(f"{_MODULE}._detect_voiceover", return_value=False):
                    result = analyze_audio("music_only.mp4")

        assert result.has_voiceover is False

    def test_music_start_forwarded_correctly(self):
        """_detect_music_start의 반환값이 AudioStats.music_start_sec에 그대로 쓰인다."""
        with patch("subprocess.run", side_effect=_make_subprocess_side_effect()):
            with patch(f"{_MODULE}._detect_music_start", return_value=1.23):
                with patch(f"{_MODULE}._detect_voiceover", return_value=False):
                    result = analyze_audio("late_start.mp4")

        assert result.music_start_sec == pytest.approx(1.23)


# ---------------------------------------------------------------------------
# 테스트: 예외 처리 → 안전한 기본값
# ---------------------------------------------------------------------------

class TestExceptionHandling:
    """예외 발생 시 안전한 기본값 반환."""

    def test_subprocess_exception_returns_defaults(self):
        """subprocess.run이 예외를 던지면 기본 AudioStats를 반환한다 (요구사항 4.4)."""
        with patch("subprocess.run", side_effect=OSError("ffprobe not found")):
            result = analyze_audio("missing.mp4")

        assert result == AudioStats(music_start_sec=0.0, target_lufs=-23.0, has_voiceover=False)

    def test_timeout_exception_returns_defaults(self):
        """ffmpeg 타임아웃도 기본 AudioStats를 반환한다."""
        import subprocess as sp

        with patch("subprocess.run", side_effect=sp.TimeoutExpired("ffprobe", 30)):
            result = analyze_audio("slow.mp4")

        assert result == AudioStats(music_start_sec=0.0, target_lufs=-23.0, has_voiceover=False)

    def test_lufs_parse_failure_uses_default(self):
        """loudnorm JSON이 없으면 target_lufs=-23.0을 사용한다."""
        def side_effect(cmd, **kwargs):
            mock = MagicMock()
            if "ffprobe" in cmd[0]:
                mock.stdout = _ffprobe_response(has_audio=True)
            elif "ffmpeg" in cmd[0] and "loudnorm" in " ".join(cmd):
                mock.stderr = "No JSON here at all"
                mock.stdout = ""
            else:
                mock.stdout = b"fake"
            return mock

        with patch("subprocess.run", side_effect=side_effect):
            with patch(f"{_MODULE}._detect_music_start", return_value=0.0):
                with patch(f"{_MODULE}._detect_voiceover", return_value=False):
                    result = analyze_audio("bad_loudnorm.mp4")

        assert result.target_lufs == pytest.approx(-23.0)

    def test_detect_music_start_exception_handled(self):
        """_detect_music_start가 예외를 던져도 최종 기본값을 반환한다."""
        with patch("subprocess.run", side_effect=_make_subprocess_side_effect()):
            with patch(f"{_MODULE}._detect_music_start", side_effect=RuntimeError("librosa error")):
                with patch(f"{_MODULE}._detect_voiceover", return_value=False):
                    result = analyze_audio("bad_onset.mp4")

        # 내부 예외는 analyze_audio 최외곽 try/except에서 잡혀 기본값 반환
        assert result == AudioStats(music_start_sec=0.0, target_lufs=-23.0, has_voiceover=False)


# ---------------------------------------------------------------------------
# 테스트: AudioStats 데이터클래스
# ---------------------------------------------------------------------------

class TestAudioStatsDataclass:
    """AudioStats 데이터클래스 기본 특성."""

    def test_fields_exist(self):
        stats = AudioStats(music_start_sec=1.2, target_lufs=-16.0, has_voiceover=True)
        assert stats.music_start_sec == pytest.approx(1.2)
        assert stats.target_lufs == pytest.approx(-16.0)
        assert stats.has_voiceover is True

    def test_equality(self):
        a = AudioStats(0.0, -23.0, False)
        b = AudioStats(0.0, -23.0, False)
        assert a == b

    def test_inequality(self):
        a = AudioStats(0.5, -14.0, True)
        b = AudioStats(0.0, -23.0, False)
        assert a != b

"""
tests/test_probe.py — probe.py 단위 테스트

subprocess를 모킹해 ffprobe 의존 없이 순수 로직을 검증한다.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.analyze.probe import ProbeResult, _parse_fraction, _simplify_aspect_ratio, probe, to_format_section
from src.common.exceptions import UnprocessableRefError


# ─── ffprobe JSON 픽스처 헬퍼 ────────────────────────────────────────────────


def _make_ffprobe_output(
    *,
    width: int = 1080,
    height: int = 1920,
    r_frame_rate: str = "30/1",
    duration: str = "12.0",
    codec_name: str = "h264",
) -> str:
    """ffprobe가 반환할 JSON 출력을 조립한다."""
    data = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec_name,
                "width": width,
                "height": height,
                "r_frame_rate": r_frame_rate,
                "duration": duration,
            }
        ],
        "format": {
            "duration": duration,
        },
    }
    return json.dumps(data)


def _make_subprocess_result(stdout: str, returncode: int = 0) -> MagicMock:
    """subprocess.run의 반환값을 모킹한다."""
    mock_result = MagicMock()
    mock_result.returncode = returncode
    mock_result.stdout = stdout
    mock_result.stderr = ""
    return mock_result


# ─── probe() 테스트 ───────────────────────────────────────────────────────────


class TestProbe:
    """probe() 함수 단위 테스트."""

    def test_valid_mp4_returns_correct_probe_result(self, tmp_path):
        """유효한 mp4 파일에 대해 ProbeResult를 올바르게 반환한다."""
        mp4_file = tmp_path / "test.mp4"
        mp4_file.touch()

        ffprobe_json = _make_ffprobe_output(
            width=1080,
            height=1920,
            r_frame_rate="30/1",
            duration="12.5",
            codec_name="h264",
        )

        with patch("subprocess.run", return_value=_make_subprocess_result(ffprobe_json)):
            result = probe(str(mp4_file))

        assert isinstance(result, ProbeResult)
        assert result.path == str(mp4_file)
        assert result.width == 1080
        assert result.height == 1920
        assert result.fps == pytest.approx(30.0)
        assert result.duration_sec == pytest.approx(12.5)
        assert result.vcodec == "h264"

    def test_missing_file_raises_unprocessable_ref_error(self, tmp_path):
        """존재하지 않는 파일은 UnprocessableRefError를 raise한다."""
        missing = str(tmp_path / "nonexistent.mp4")

        with pytest.raises(UnprocessableRefError) as exc_info:
            probe(missing)

        assert exc_info.value.path == missing
        assert "존재하지 않습니다" in str(exc_info.value)

    def test_non_mp4_extension_raises_unprocessable_ref_error(self, tmp_path):
        """비-mp4 확장자 파일은 UnprocessableRefError를 raise한다."""
        avi_file = tmp_path / "video.avi"
        avi_file.touch()

        with pytest.raises(UnprocessableRefError) as exc_info:
            probe(str(avi_file))

        assert exc_info.value.path == str(avi_file)
        assert ".mp4" in str(exc_info.value)

    def test_non_mp4_uppercase_extension_raises_error(self, tmp_path):
        """.MP4 대문자 확장자도 통과한다 (대소문자 무관)."""
        mp4_file = tmp_path / "test.MP4"
        mp4_file.touch()

        ffprobe_json = _make_ffprobe_output()

        with patch("subprocess.run", return_value=_make_subprocess_result(ffprobe_json)):
            result = probe(str(mp4_file))

        assert result.width == 1080

    def test_mov_extension_raises_unprocessable_ref_error(self, tmp_path):
        """.mov 확장자는 UnprocessableRefError를 raise한다."""
        mov_file = tmp_path / "video.mov"
        mov_file.touch()

        with pytest.raises(UnprocessableRefError):
            probe(str(mov_file))

    def test_ffprobe_nonzero_exit_raises_unprocessable_ref_error(self, tmp_path):
        """ffprobe가 비-zero 종료코드를 반환하면 UnprocessableRefError를 raise한다."""
        mp4_file = tmp_path / "broken.mp4"
        mp4_file.touch()

        with patch(
            "subprocess.run",
            return_value=_make_subprocess_result("", returncode=1),
        ):
            with pytest.raises(UnprocessableRefError) as exc_info:
                probe(str(mp4_file))

        assert "실패" in str(exc_info.value)

    def test_no_video_stream_raises_unprocessable_ref_error(self, tmp_path):
        """비디오 스트림이 없는 경우 UnprocessableRefError를 raise한다."""
        mp4_file = tmp_path / "audio_only.mp4"
        mp4_file.touch()

        data = {
            "streams": [
                {"codec_type": "audio", "codec_name": "aac"}
            ],
            "format": {"duration": "10.0"},
        }
        ffprobe_json = json.dumps(data)

        with patch("subprocess.run", return_value=_make_subprocess_result(ffprobe_json)):
            with pytest.raises(UnprocessableRefError) as exc_info:
                probe(str(mp4_file))

        assert "비디오 스트림" in str(exc_info.value)

    def test_fractional_fps_parsed_correctly(self, tmp_path):
        """30000/1001 형태의 fps가 올바르게 파싱된다 (29.97fps)."""
        mp4_file = tmp_path / "test.mp4"
        mp4_file.touch()

        ffprobe_json = _make_ffprobe_output(r_frame_rate="30000/1001")

        with patch("subprocess.run", return_value=_make_subprocess_result(ffprobe_json)):
            result = probe(str(mp4_file))

        assert result.fps == pytest.approx(30000 / 1001, rel=1e-3)

    def test_probe_result_has_correct_path(self, tmp_path):
        """ProbeResult.path는 입력 경로와 일치한다."""
        mp4_file = tmp_path / "myfile.mp4"
        mp4_file.touch()

        ffprobe_json = _make_ffprobe_output()

        with patch("subprocess.run", return_value=_make_subprocess_result(ffprobe_json)):
            result = probe(str(mp4_file))

        assert result.path == str(mp4_file)


# ─── to_format_section() 테스트 ──────────────────────────────────────────────


class TestToFormatSection:
    """to_format_section() 함수 단위 테스트."""

    def _make_probe_result(
        self,
        *,
        path: str = "test.mp4",
        width: int = 1080,
        height: int = 1920,
        fps: float = 30.0,
        duration_sec: float = 12.0,
        vcodec: str = "h264",
    ) -> ProbeResult:
        return ProbeResult(
            path=path,
            width=width,
            height=height,
            fps=fps,
            duration_sec=duration_sec,
            vcodec=vcodec,
        )

    def test_returns_all_required_keys(self):
        """반환 dict에 aspect_ratio·resolution·fps·duration_sec_range가 모두 있다."""
        results = [self._make_probe_result()]
        section = to_format_section(results)

        assert "aspect_ratio" in section
        assert "resolution" in section
        assert "fps" in section
        assert "duration_sec_range" in section

    def test_aspect_ratio_9_16_for_standard_portrait(self):
        """1080x1920은 9:16으로 정규화된다."""
        results = [self._make_probe_result(width=1080, height=1920)]
        section = to_format_section(results)

        assert section["aspect_ratio"] == "9:16"

    def test_resolution_format(self):
        """resolution은 'WxH' 형식이다."""
        results = [self._make_probe_result(width=1080, height=1920)]
        section = to_format_section(results)

        assert section["resolution"] == "1080x1920"

    def test_fps_is_average_across_results(self):
        """fps는 모든 결과의 평균값이다."""
        results = [
            self._make_probe_result(fps=30.0),
            self._make_probe_result(fps=24.0),
        ]
        section = to_format_section(results)

        assert section["fps"] == pytest.approx(27.0)

    def test_duration_sec_range_is_min_max(self):
        """duration_sec_range는 [최솟값, 최댓값]이다."""
        results = [
            self._make_probe_result(duration_sec=10.0),
            self._make_probe_result(duration_sec=15.0),
            self._make_probe_result(duration_sec=12.5),
        ]
        section = to_format_section(results)

        assert section["duration_sec_range"] == [10.0, 15.0]

    def test_single_result_duration_range_is_same_value(self):
        """단일 결과의 duration_sec_range는 [same, same]이다."""
        results = [self._make_probe_result(duration_sec=11.3)]
        section = to_format_section(results)

        assert section["duration_sec_range"][0] == pytest.approx(11.3)
        assert section["duration_sec_range"][1] == pytest.approx(11.3)

    def test_empty_results_returns_defaults(self):
        """빈 리스트는 기본값을 반환한다."""
        section = to_format_section([])

        assert section["aspect_ratio"] == "9:16"
        assert section["fps"] == 30.0
        assert section["duration_sec_range"] == [0.0, 0.0]

    def test_most_common_resolution_is_used(self):
        """최빈 해상도가 resolution·aspect_ratio 산출에 사용된다."""
        results = [
            self._make_probe_result(width=1080, height=1920),
            self._make_probe_result(width=1080, height=1920),
            self._make_probe_result(width=720, height=1280),
        ]
        section = to_format_section(results)

        assert section["resolution"] == "1080x1920"

    def test_duration_sec_range_covers_all_values(self):
        """duration_sec_range는 모든 duration 값을 포괄한다."""
        durations = [8.5, 11.0, 13.2, 9.7, 14.8]
        results = [self._make_probe_result(duration_sec=d) for d in durations]
        section = to_format_section(results)

        lo, hi = section["duration_sec_range"]
        assert lo <= min(durations)
        assert hi >= max(durations)


# ─── 내부 헬퍼 테스트 ────────────────────────────────────────────────────────


class TestParseFraction:
    def test_integer_fps(self):
        assert _parse_fraction("30/1") == pytest.approx(30.0)

    def test_ntsc_fps(self):
        assert _parse_fraction("30000/1001") == pytest.approx(29.97, rel=1e-3)

    def test_24fps(self):
        assert _parse_fraction("24/1") == pytest.approx(24.0)

    def test_invalid_returns_zero(self):
        assert _parse_fraction("invalid") == pytest.approx(0.0)

    def test_zero_denominator_returns_zero(self):
        assert _parse_fraction("30/0") == pytest.approx(0.0)


class TestSimplifyAspectRatio:
    def test_9_16_portrait(self):
        assert _simplify_aspect_ratio(1080, 1920) == "9:16"

    def test_16_9_landscape(self):
        assert _simplify_aspect_ratio(1920, 1080) == "16:9"

    def test_zero_width_returns_default(self):
        assert _simplify_aspect_ratio(0, 1920) == "9:16"

    def test_zero_height_returns_default(self):
        assert _simplify_aspect_ratio(1080, 0) == "9:16"

    def test_720p_portrait(self):
        # 720x1280 = 9:16
        assert _simplify_aspect_ratio(720, 1280) == "9:16"

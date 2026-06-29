"""
tests/test_cut_detect.py — src/analyze/cut_detect.py 단위 테스트

요구사항: 3.1, 3.2, 3.3, 3.4, 3.5
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.analyze.cut_detect import (
    compute_pacing_metrics,
    detect_cuts,
)


# ---------------------------------------------------------------------------
# detect_cuts (subprocess 모킹)
# ---------------------------------------------------------------------------

class TestDetectCuts:
    def _make_ffmpeg_output(self, pts_times: list[float]) -> str:
        """pts_time:<t> 형식 라인들을 포함한 가짜 ffmpeg 출력을 만든다."""
        lines = []
        for t in pts_times:
            lines.append(
                f"[Parsed_showinfo_1 @ 0x...] n:0 pts:100 pts_time:{t} "
                f"pos:0 fmt:yuv420p sar:0/1 s:1080x1920 i:P iskey:0 type:P"
            )
        return "\n".join(lines)

    def test_returns_list_with_zero_when_no_cuts(self):
        """씬 감지 결과 없으면 [0.0] 반환."""
        mock_result = MagicMock()
        mock_result.stdout = "No cuts here"
        with patch("subprocess.run", return_value=mock_result):
            result = detect_cuts("fake.mp4")
        assert result == [0.0]

    def test_warning_when_no_cuts(self, caplog):
        """컷 미감지 시 경고 로그를 기록한다."""
        import logging
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            with caplog.at_level(logging.WARNING, logger="src.analyze.cut_detect"):
                result = detect_cuts("fake.mp4")
        assert "single shot" in caplog.text.lower() or "No cuts detected" in caplog.text

    def test_parses_pts_times_correctly(self):
        """pts_time 파싱 후 0.0 포함 정렬 반환."""
        mock_result = MagicMock()
        mock_result.stdout = self._make_ffmpeg_output([1.5, 3.2, 5.8])
        with patch("subprocess.run", return_value=mock_result):
            result = detect_cuts("fake.mp4")
        assert result == [0.0, 1.5, 3.2, 5.8]

    def test_always_prepends_zero(self):
        """0.0은 항상 첫 원소여야 한다."""
        mock_result = MagicMock()
        mock_result.stdout = self._make_ffmpeg_output([2.0, 4.0])
        with patch("subprocess.run", return_value=mock_result):
            result = detect_cuts("fake.mp4")
        assert result[0] == 0.0

    def test_deduplicates_timestamps(self):
        """중복 타임스탬프는 제거된다."""
        mock_result = MagicMock()
        mock_result.stdout = self._make_ffmpeg_output([1.0, 1.0, 2.0])
        with patch("subprocess.run", return_value=mock_result):
            result = detect_cuts("fake.mp4")
        assert result.count(1.0) == 1

    def test_result_is_sorted(self):
        """반환 리스트는 항상 오름차순 정렬이어야 한다."""
        mock_result = MagicMock()
        mock_result.stdout = self._make_ffmpeg_output([5.0, 1.0, 3.0])
        with patch("subprocess.run", return_value=mock_result):
            result = detect_cuts("fake.mp4")
        assert result == sorted(result)

    def test_custom_threshold_passed_to_ffmpeg(self):
        """scene_threshold가 ffmpeg 명령에 전달되는지 확인."""
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            detect_cuts("fake.mp4", scene_threshold=0.5)
        cmd_args = mock_run.call_args[0][0]
        assert "0.5" in " ".join(cmd_args)

    def test_ffmpeg_not_found_returns_single_shot(self, caplog):
        """ffmpeg 실행 파일 없으면 [0.0] 반환 및 경고."""
        import logging
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with caplog.at_level(logging.WARNING, logger="src.analyze.cut_detect"):
                result = detect_cuts("fake.mp4")
        assert result == [0.0]

    def test_zero_not_double_inserted(self):
        """ffmpeg 출력에 pts_time:0.0이 포함돼도 0.0이 중복 삽입되지 않는다."""
        mock_result = MagicMock()
        mock_result.stdout = self._make_ffmpeg_output([0.0, 1.0, 2.0])
        with patch("subprocess.run", return_value=mock_result):
            result = detect_cuts("fake.mp4")
        assert result.count(0.0) == 1


# ---------------------------------------------------------------------------
# compute_pacing_metrics
# ---------------------------------------------------------------------------

class TestComputePacingMetrics:
    def test_single_cut_single_shot(self):
        """[0.0] → cut_count=1, avg=duration."""
        m = compute_pacing_metrics([0.0], duration=10.0)
        assert m["cut_count"] == 1
        assert m["avg_shot_len_sec"] == pytest.approx(10.0)

    def test_two_timestamps_one_cut(self):
        """[0.0, 5.0] + duration=10 → cut_count=1 (전환 1회), 숏 2개."""
        m = compute_pacing_metrics([0.0, 5.0], duration=10.0)
        # cut_count = len(cuts) - 1 = 1 (설계 명세)
        assert m["cut_count"] == 1
        # avg_shot_len_sec = duration / cut_count = 10 / 1 = 10.0
        assert m["avg_shot_len_sec"] == pytest.approx(10.0)
        # 숏 길이 분포는 2개 구간: [0→5]=5.0, [5→10]=5.0
        assert len(m["shot_len_distribution_sec"]) == 2

    def test_shot_lengths_sum_equals_duration(self):
        """숏 길이 합 = duration (부동소수 허용오차 내)."""
        cuts = [0.0, 2.0, 5.0, 8.0]
        duration = 10.0
        m = compute_pacing_metrics(cuts, duration=duration)
        total = sum(m["shot_len_distribution_sec"])
        assert total == pytest.approx(duration, abs=1e-9)

    def test_all_shot_lengths_non_negative(self):
        """모든 숏 길이 ≥ 0."""
        m = compute_pacing_metrics([0.0, 1.0, 3.0, 7.0], duration=9.0)
        for length in m["shot_len_distribution_sec"]:
            assert length >= 0.0

    def test_shot_distribution_length_is_cuts_count(self):
        """shot_len_distribution_sec 길이 = len(cuts) (구간 수 = 컷 수 + 1 이 아님).
        
        설계 명세: shot_len_distribution_sec = [cuts[i+1]-cuts[i] for i...] + [duration - cuts[-1]]
        cuts=[0.0, 1.0, 2.0, 3.0] → 구간 3개 + 마지막 1개 = 4개 = len(cuts)
        """
        cuts = [0.0, 1.0, 2.0, 3.0]
        m = compute_pacing_metrics(cuts, duration=5.0)
        # shot_len_distribution_sec 길이 = len(cuts) = 4
        assert len(m["shot_len_distribution_sec"]) == len(cuts)

    def test_avg_shot_len_equals_duration_div_cut_count(self):
        """avg_shot_len_sec = duration / cut_count."""
        cuts = [0.0, 2.0, 4.0]
        duration = 6.0
        m = compute_pacing_metrics(cuts, duration=duration)
        assert m["avg_shot_len_sec"] == pytest.approx(duration / m["cut_count"])

    # --- hook_cut_density ---

    def test_hook_density_high_three_cuts_in_first_3s(self):
        """첫 3초 내 컷 3개 이상 → high."""
        m = compute_pacing_metrics([0.0, 0.5, 1.5, 2.5, 5.0], duration=10.0)
        assert m["hook_cut_density"] == "high"

    def test_hook_density_medium_one_cut_in_first_3s(self):
        """첫 3초 내 컷 1개 → medium."""
        m = compute_pacing_metrics([0.0, 1.5, 6.0], duration=10.0)
        assert m["hook_cut_density"] == "medium"

    def test_hook_density_medium_two_cuts_in_first_3s(self):
        """첫 3초 내 컷 2개 → medium."""
        m = compute_pacing_metrics([0.0, 1.0, 2.0, 7.0], duration=10.0)
        assert m["hook_cut_density"] == "medium"

    def test_hook_density_low_no_cuts_in_first_3s(self):
        """첫 3초 내 컷 0개 → low."""
        m = compute_pacing_metrics([0.0, 5.0, 8.0], duration=10.0)
        assert m["hook_cut_density"] == "low"

    def test_hook_density_boundary_exactly_3s(self):
        """pts_time=3.0은 첫 3초 내에 포함되지 않는다 (< 3.0)."""
        # 3.0 초는 _HOOK_WINDOW_SEC=3.0 이고 조건이 t < 3.0이므로 제외
        m = compute_pacing_metrics([0.0, 3.0, 6.0], duration=10.0)
        assert m["hook_cut_density"] == "low"

    def test_returns_required_keys(self):
        """반환 dict에 필수 키가 모두 존재한다."""
        m = compute_pacing_metrics([0.0], duration=5.0)
        assert "cut_count" in m
        assert "cut_count_range" in m
        assert "avg_shot_len_sec" in m
        assert "shot_len_distribution_sec" in m
        assert "hook_cut_density" in m
        assert "rhythm_mode" in m

    def test_cut_count_range_equals_cut_count(self):
        """단일 레퍼런스이므로 cut_count_range = [cut_count, cut_count]."""
        m = compute_pacing_metrics([0.0, 1.0, 2.0], duration=5.0)
        assert m["cut_count_range"] == [m["cut_count"], m["cut_count"]]

    def test_rhythm_mode_fast_montage(self):
        """avg_shot_len_sec < 1.5 → fast_montage."""
        # 10컷, 10초 → avg = 1.0
        cuts = [0.0] + [i * 1.0 for i in range(1, 11)]
        m = compute_pacing_metrics(cuts, duration=10.0)
        assert m["rhythm_mode"] == "fast_montage"

    def test_rhythm_mode_slow_hold(self):
        """avg_shot_len_sec > 3.0 → slow_hold."""
        # 2컷, 10초 → avg = 5.0
        m = compute_pacing_metrics([0.0, 5.0], duration=10.0)
        assert m["rhythm_mode"] == "slow_hold"

    def test_rhythm_mode_mixed(self):
        """1.5 ≤ avg_shot_len_sec ≤ 3.0 → mixed."""
        # 5컷, 10초 → avg = 2.0
        m = compute_pacing_metrics([0.0, 2.0, 4.0, 6.0, 8.0], duration=10.0)
        assert m["rhythm_mode"] == "mixed"


# ---------------------------------------------------------------------------
# merge_pacing — 삭제됨. 1 ref → 1 JSON 설계로 변경.
# ---------------------------------------------------------------------------

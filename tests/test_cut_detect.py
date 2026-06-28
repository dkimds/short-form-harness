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
    merge_pacing,
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
        assert "avg_shot_len_sec" in m
        assert "shot_len_distribution_sec" in m
        assert "hook_cut_density" in m


# ---------------------------------------------------------------------------
# merge_pacing
# ---------------------------------------------------------------------------

class TestMergePacing:
    def _make_metrics(
        self,
        cut_count: int,
        avg_shot_len_sec: float,
        hook_cut_density: str = "low",
    ) -> dict:
        return {
            "cut_count": cut_count,
            "avg_shot_len_sec": avg_shot_len_sec,
            "shot_len_distribution_sec": [avg_shot_len_sec] * cut_count,
            "hook_cut_density": hook_cut_density,
        }

    def test_empty_list_returns_defaults(self):
        """빈 리스트 입력 시 기본값 반환."""
        result = merge_pacing([])
        assert result["cut_count_range"] == [0, 0]
        assert result["rhythm_mode"] == "mixed"

    def test_cut_count_range_covers_all_values(self):
        """cut_count_range는 모든 관측값을 포괄한다."""
        per_ref = [
            self._make_metrics(3, 2.0),
            self._make_metrics(10, 0.8),
            self._make_metrics(5, 1.5),
        ]
        result = merge_pacing(per_ref)
        assert result["cut_count_range"][0] == 3
        assert result["cut_count_range"][1] == 10

    def test_cut_count_range_min_le_max(self):
        """range[0] ≤ range[1] 불변식."""
        per_ref = [self._make_metrics(7, 1.0)]
        result = merge_pacing(per_ref)
        assert result["cut_count_range"][0] <= result["cut_count_range"][1]

    def test_rhythm_mode_fast_montage(self):
        """avg_shot_len_sec < 1.5 → fast_montage."""
        per_ref = [self._make_metrics(10, 0.9)]
        result = merge_pacing(per_ref)
        assert result["rhythm_mode"] == "fast_montage"

    def test_rhythm_mode_slow_hold(self):
        """avg_shot_len_sec > 3.0 → slow_hold."""
        per_ref = [self._make_metrics(3, 3.5)]
        result = merge_pacing(per_ref)
        assert result["rhythm_mode"] == "slow_hold"

    def test_rhythm_mode_mixed(self):
        """1.5 ≤ avg_shot_len_sec ≤ 3.0 → mixed."""
        per_ref = [self._make_metrics(5, 2.0)]
        result = merge_pacing(per_ref)
        assert result["rhythm_mode"] == "mixed"

    def test_rhythm_mode_boundary_exactly_1_5(self):
        """avg_shot_len_sec = 1.5 → mixed (fast_montage는 < 1.5)."""
        per_ref = [self._make_metrics(5, 1.5)]
        result = merge_pacing(per_ref)
        assert result["rhythm_mode"] == "mixed"

    def test_rhythm_mode_boundary_exactly_3_0(self):
        """avg_shot_len_sec = 3.0 → mixed (slow_hold는 > 3.0)."""
        per_ref = [self._make_metrics(3, 3.0)]
        result = merge_pacing(per_ref)
        assert result["rhythm_mode"] == "mixed"

    def test_rhythm_mode_enum_values(self):
        """rhythm_mode는 세 가지 enum 값 중 하나여야 한다."""
        valid = {"fast_montage", "slow_hold", "mixed"}
        for avg in [0.5, 1.4, 1.5, 2.0, 3.0, 3.1, 5.0]:
            result = merge_pacing([self._make_metrics(5, avg)])
            assert result["rhythm_mode"] in valid

    def test_avg_shot_len_sec_is_mean(self):
        """avg_shot_len_sec는 입력들의 평균이다."""
        per_ref = [
            self._make_metrics(5, 1.0),
            self._make_metrics(5, 3.0),
        ]
        result = merge_pacing(per_ref)
        assert result["avg_shot_len_sec"] == pytest.approx(2.0)

    def test_shot_len_distribution_sorted(self):
        """shot_len_distribution_sec는 정렬되어 있다."""
        per_ref = [
            self._make_metrics(2, 3.0),
            self._make_metrics(3, 1.0),
        ]
        result = merge_pacing(per_ref)
        dist = result["shot_len_distribution_sec"]
        assert dist == sorted(dist)

    def test_hook_density_mode_high_wins(self):
        """최빈값이 동률일 때 high > medium > low 우선."""
        per_ref = [
            self._make_metrics(5, 1.0, "high"),
            self._make_metrics(5, 1.0, "medium"),
        ]
        result = merge_pacing(per_ref)
        # 동률이면 high 우선
        assert result["hook_cut_density"] == "high"

    def test_hook_density_majority_wins(self):
        """최빈값이 명확하면 그 값을 반환한다."""
        per_ref = [
            self._make_metrics(5, 2.0, "low"),
            self._make_metrics(5, 2.0, "low"),
            self._make_metrics(5, 2.0, "high"),
        ]
        result = merge_pacing(per_ref)
        assert result["hook_cut_density"] == "low"

    def test_hook_density_valid_values(self):
        """hook_cut_density는 세 가지 값 중 하나."""
        valid = {"high", "medium", "low"}
        for density in ["high", "medium", "low"]:
            result = merge_pacing([self._make_metrics(5, 2.0, density)])
            assert result["hook_cut_density"] in valid

    def test_single_ref_passthrough(self):
        """레퍼런스 1개면 해당 값이 그대로 반영된다."""
        per_ref = [self._make_metrics(8, 0.9, "high")]
        result = merge_pacing(per_ref)
        assert result["cut_count_range"] == [8, 8]
        assert result["avg_shot_len_sec"] == pytest.approx(0.9)
        assert result["hook_cut_density"] == "high"

    def test_returns_required_keys(self):
        """반환 dict에 필수 키가 모두 존재한다."""
        result = merge_pacing([self._make_metrics(5, 2.0)])
        assert "cut_count_range" in result
        assert "avg_shot_len_sec" in result
        assert "shot_len_distribution_sec" in result
        assert "rhythm_mode" in result
        assert "hook_cut_density" in result

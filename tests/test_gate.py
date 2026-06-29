"""
tests/test_gate.py — gate.py 단위 테스트

결정론적 검사, 비전 자기판정, run_gate, run_with_retry 동작을 검증한다.
ffprobe subprocess 호출 및 VendorClient는 모킹해 외부 의존성을 제거한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from src.common.exceptions import VendorError
from src.generate.gate import (
    GateResult,
    deterministic_check,
    run_gate,
    run_with_retry,
    vision_judge,
)


# ---------------------------------------------------------------------------
# 헬퍼 / 픽스처
# ---------------------------------------------------------------------------

def _make_profile(
    duration_range: list[float] | None = None,
    cut_count_range: list[int] | None = None,
    avg_shot_len: float = 2.0,
) -> dict:
    """테스트용 최소 style_profile dict를 반환한다."""
    return {
        "format": {
            "aspect_ratio": "9:16",
            "duration_sec_range": duration_range or [10.0, 15.0],
        },
        "pacing": {
            "cut_count_range": cut_count_range or [4, 12],
            "avg_shot_len_sec": avg_shot_len,
        },
        "audio": {
            "music_mood": "upbeat_light_kpop_inspired",
            "has_voiceover": True,
        },
        "visual": {
            "color_grade": "warm_soft_aesthetic",
            "lighting": "natural_window_soft",
        },
        "narrative": {
            "beats": [
                {"role": "hook"},
                {"role": "application"},
                {"role": "result_glow"},
                {"role": "product_hero"},
                {"role": "application"},
                {"role": "result_glow"},
            ]
        },
    }


def _make_ffprobe_output(width: int, height: int, duration: float) -> str:
    """ffprobe JSON 출력을 시뮬레이션한다."""
    return json.dumps({
        "streams": [
            {
                "codec_type": "video",
                "width": width,
                "height": height,
                "duration": str(duration),
            }
        ]
    })


def _make_client(verdict: str = "pass", reasons: list[str] | None = None) -> MagicMock:
    """모킹된 VendorClient를 반환한다."""
    client = MagicMock()
    client.judge_video.return_value = {
        "verdict": verdict,
        "reasons": reasons or ["mood match: OK", "captions: visible", "visual: matches"],
    }
    return client


# ---------------------------------------------------------------------------
# deterministic_check 테스트
# ---------------------------------------------------------------------------

class TestDeterministicCheck:
    def test_passes_for_valid_9_16_mp4(self):
        """유효한 9:16 비율, 정상 duration, 정상 cut_count → passed=True."""
        profile = _make_profile(duration_range=[10.0, 15.0], avg_shot_len=2.0)
        # 576×1024 = 9:16, duration=12.0 → 12/2=6 컷 (4~12 범위 내)
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = deterministic_check("test.mp4", profile)

        assert result["passed"] is True
        assert result["aspect_ratio"]["passed"] is True
        assert result["duration"]["passed"] is True
        assert result["cut_count"]["passed"] is True

    def test_fails_for_wrong_aspect_ratio(self):
        """16:9 가로 비율 (1920×1080) → aspect_ratio passed=False."""
        profile = _make_profile(duration_range=[10.0, 15.0])
        ffprobe_out = _make_ffprobe_output(1920, 1080, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = deterministic_check("test.mp4", profile)

        assert result["aspect_ratio"]["passed"] is False
        assert result["passed"] is False

    def test_fails_for_duration_out_of_range(self):
        """duration=5.0 (범위 10~15 밖) → duration passed=False."""
        profile = _make_profile(duration_range=[10.0, 15.0])
        ffprobe_out = _make_ffprobe_output(576, 1024, 5.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = deterministic_check("test.mp4", profile)

        assert result["duration"]["passed"] is False
        assert result["passed"] is False

    def test_measured_aspect_ratio_string_format(self):
        """측정된 aspect_ratio는 'W:H' 형식이다."""
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = deterministic_check("test.mp4", profile)

        assert result["aspect_ratio"]["measured"] == "576:1024"

    def test_measured_duration_is_float(self):
        """측정된 duration은 float이다."""
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.5)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = deterministic_check("test.mp4", profile)

        assert isinstance(result["duration"]["measured"], float)
        assert abs(result["duration"]["measured"] - 12.5) < 0.01

    def test_cut_count_estimated_from_duration(self):
        """cut_count는 duration / avg_shot_len_sec 로 추정된다."""
        # duration=12, avg_shot=2.0 → 6컷
        profile = _make_profile(avg_shot_len=2.0, cut_count_range=[4, 12])
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = deterministic_check("test.mp4", profile)

        assert result["cut_count"]["estimated"] == 6

    def test_graceful_on_ffprobe_not_found(self):
        """ffprobe가 없으면 passed=True로 graceful 처리한다."""
        profile = _make_profile()

        with patch("subprocess.run", side_effect=FileNotFoundError("ffprobe not found")):
            result = deterministic_check("test.mp4", profile)

        assert result["passed"] is True
        assert "warning" in result["aspect_ratio"]

    def test_graceful_on_subprocess_error(self):
        """subprocess.SubprocessError 발생 시 passed=True로 graceful 처리한다."""
        import subprocess as sp
        profile = _make_profile()

        with patch("subprocess.run", side_effect=sp.SubprocessError("timeout")):
            result = deterministic_check("test.mp4", profile)

        assert result["passed"] is True

    def test_returns_dict_with_required_keys(self):
        """반환값에 필수 키가 모두 있다."""
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = deterministic_check("test.mp4", profile)

        assert "aspect_ratio" in result
        assert "duration" in result
        assert "cut_count" in result
        assert "passed" in result


# ---------------------------------------------------------------------------
# vision_judge 테스트
# ---------------------------------------------------------------------------

class TestVisionJudge:
    def test_returns_pass_on_successful_api_call(self, tmp_path):
        """API 호출 성공 시 verdict=pass를 반환한다."""
        client = _make_client(verdict="pass")
        profile = _make_profile()

        result = vision_judge(client, "test.mp4", profile)

        assert result["verdict"] == "pass"
        assert isinstance(result["reasons"], list)

    def test_returns_pass_on_vendor_error(self, tmp_path):
        """VendorError 발생 시 verdict=pass (skipped)를 반환한다."""
        client = MagicMock()
        client.judge_video.side_effect = VendorError("API 실패", vendor="Gemini")
        profile = _make_profile()

        result = vision_judge(client, "test.mp4", profile)

        assert result["verdict"] == "pass"
        assert any("skipped" in r for r in result["reasons"])

    def test_fills_music_mood_placeholder(self):
        """프롬프트에 music_mood 플레이스홀더가 채워진다."""
        client = _make_client()
        profile = _make_profile()
        captured_prompts = []

        def capture_call(video_path, prompt):
            captured_prompts.append(prompt)
            return {"verdict": "pass", "reasons": []}

        client.judge_video.side_effect = capture_call
        vision_judge(client, "test.mp4", profile)

        assert len(captured_prompts) == 1
        assert "upbeat_light_kpop_inspired" in captured_prompts[0]

    def test_fills_color_grade_placeholder(self):
        """프롬프트에 color_grade 플레이스홀더가 채워진다."""
        client = _make_client()
        profile = _make_profile()
        captured_prompts = []

        def capture_call(video_path, prompt):
            captured_prompts.append(prompt)
            return {"verdict": "pass", "reasons": []}

        client.judge_video.side_effect = capture_call
        vision_judge(client, "test.mp4", profile)

        assert "warm_soft_aesthetic" in captured_prompts[0]

    def test_fills_beat_count_placeholder(self):
        """프롬프트에 beat_count 플레이스홀더가 채워진다."""
        client = _make_client()
        profile = _make_profile()  # 6개의 beats
        captured_prompts = []

        def capture_call(video_path, prompt):
            captured_prompts.append(prompt)
            return {"verdict": "pass", "reasons": []}

        client.judge_video.side_effect = capture_call
        vision_judge(client, "test.mp4", profile)

        assert "6" in captured_prompts[0]

    def test_returns_fail_verdict_from_api(self):
        """API가 fail을 반환하면 그대로 전달한다."""
        client = _make_client(verdict="fail", reasons=["mood mismatch"])
        profile = _make_profile()

        result = vision_judge(client, "test.mp4", profile)

        assert result["verdict"] == "fail"

    def test_judge_video_called_with_mp4_path(self):
        """judge_video는 mp4 경로를 첫 번째 인자로 받는다."""
        client = _make_client()
        profile = _make_profile()

        vision_judge(client, "my_video.mp4", profile)

        call_args = client.judge_video.call_args
        assert call_args[0][0] == "my_video.mp4"


# ---------------------------------------------------------------------------
# run_gate 테스트
# ---------------------------------------------------------------------------

class TestRunGate:
    def test_saves_gate_json(self, tmp_path):
        """run_gate는 gate.json을 run_dir에 저장한다."""
        client = _make_client(verdict="pass")
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            run_gate(client, "test.mp4", profile, str(tmp_path))

        gate_path = tmp_path / "gate.json"
        assert gate_path.exists()

    def test_gate_json_has_correct_structure(self, tmp_path):
        """저장된 gate.json에 필수 키가 모두 있다."""
        client = _make_client(verdict="pass")
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            run_gate(client, "test.mp4", profile, str(tmp_path))

        gate_data = json.loads((tmp_path / "gate.json").read_text())
        assert "passed" in gate_data
        assert "deterministic" in gate_data
        assert "vision_judgment" in gate_data
        assert "reasons" in gate_data

    def test_returns_gate_result_instance(self, tmp_path):
        """run_gate는 GateResult 인스턴스를 반환한다."""
        client = _make_client(verdict="pass")
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_gate(client, "test.mp4", profile, str(tmp_path))

        assert isinstance(result, GateResult)

    def test_returns_passed_true_when_both_checks_pass(self, tmp_path):
        """결정론적 + 비전 둘 다 통과 → passed=True."""
        client = _make_client(verdict="pass")
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_gate(client, "test.mp4", profile, str(tmp_path))

        assert result.passed is True

    def test_returns_passed_false_when_vision_fails(self, tmp_path):
        """비전 판정 fail → passed=False."""
        client = _make_client(verdict="fail", reasons=["mood mismatch"])
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_gate(client, "test.mp4", profile, str(tmp_path))

        assert result.passed is False

    def test_returns_passed_false_when_deterministic_fails(self, tmp_path):
        """결정론적 검사 fail (잘못된 비율) → passed=False."""
        client = _make_client(verdict="pass")
        profile = _make_profile()
        # 16:9 → aspect_ratio 실패
        ffprobe_out = _make_ffprobe_output(1920, 1080, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_gate(client, "test.mp4", profile, str(tmp_path))

        assert result.passed is False

    def test_reasons_populated_on_failure(self, tmp_path):
        """실패 시 reasons가 비어있지 않다."""
        client = _make_client(verdict="fail", reasons=["mood: wrong"])
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_gate(client, "test.mp4", profile, str(tmp_path))

        assert len(result.reasons) > 0


# ---------------------------------------------------------------------------
# run_with_retry 테스트
# ---------------------------------------------------------------------------

class TestRunWithRetry:
    def test_calls_generate_once_exactly_once_on_pass(self, tmp_path):
        """첫 시도에 pass하면 generate_once는 1회만 호출된다."""
        client = _make_client(verdict="pass")
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)
        generate_mock = MagicMock(return_value="test.mp4")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_with_retry(generate_mock, client, profile, str(tmp_path), max_retries=2)

        assert generate_mock.call_count == 1
        assert result.passed is True

    def test_stops_on_first_pass_mid_retry(self, tmp_path):
        """두 번째 시도에 pass하면 그 이상 시도하지 않는다."""
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)
        generate_mock = MagicMock(return_value="test.mp4")

        # 첫 번째 call → fail, 두 번째 call → pass
        fail_reasons = ["mood mismatch"]
        pass_reasons = ["all good"]
        call_count = {"n": 0}

        def judge_side_effect(video_path, prompt):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"verdict": "fail", "reasons": fail_reasons}
            return {"verdict": "pass", "reasons": pass_reasons}

        client = MagicMock()
        client.judge_video.side_effect = judge_side_effect

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_with_retry(generate_mock, client, profile, str(tmp_path), max_retries=2)

        # 2번 호출 후 pass → 3번째 시도 없음
        assert generate_mock.call_count == 2
        assert result.passed is True

    def test_retries_correct_number_of_times_on_all_fail(self, tmp_path):
        """max_retries=2이면 총 3회(초기 1 + 재시도 2) 시도한다."""
        client = _make_client(verdict="fail", reasons=["always fail"])
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)
        generate_mock = MagicMock(return_value="test.mp4")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_with_retry(generate_mock, client, profile, str(tmp_path), max_retries=2)

        assert generate_mock.call_count == 3
        assert result.passed is False

    def test_returns_last_result_on_all_failures(self, tmp_path):
        """모든 시도 실패 시 마지막 GateResult를 반환한다."""
        client = _make_client(verdict="fail", reasons=["consistently bad"])
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)
        generate_mock = MagicMock(return_value="test.mp4")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_with_retry(generate_mock, client, profile, str(tmp_path), max_retries=1)

        assert isinstance(result, GateResult)
        assert result.passed is False

    def test_zero_retries_calls_once(self, tmp_path):
        """max_retries=0이면 generate_once는 정확히 1회만 호출된다."""
        client = _make_client(verdict="fail")
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)
        generate_mock = MagicMock(return_value="test.mp4")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_with_retry(generate_mock, client, profile, str(tmp_path), max_retries=0)

        assert generate_mock.call_count == 1

    def test_retry_count_on_result(self, tmp_path):
        """GateResult.retry_count는 0-indexed 시도 번호를 반영한다."""
        client = _make_client(verdict="fail")
        profile = _make_profile()
        ffprobe_out = _make_ffprobe_output(576, 1024, 12.0)
        generate_mock = MagicMock(return_value="test.mp4")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ffprobe_out, returncode=0)
            result = run_with_retry(generate_mock, client, profile, str(tmp_path), max_retries=2)

        # 3번 시도, 모두 실패 → 마지막 시도 index=2
        assert result.retry_count == 2


# ---------------------------------------------------------------------------
# 격리 불변식 테스트 (요구사항 13.2)
# ---------------------------------------------------------------------------

class TestIsolationInvariant:
    def test_gate_does_not_import_analyze(self):
        """gate.py 소스코드에 src.analyze import가 없다 (요구사항 13.2)."""
        import inspect
        import src.generate.gate as gate_module

        source = inspect.getsource(gate_module)
        assert "src.analyze" not in source, (
            "gate.py 소스에 src.analyze import가 발견됨"
        )

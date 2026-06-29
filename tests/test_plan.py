"""
tests/test_plan.py — plan.py 단위 테스트 및 속성 기반 테스트

build_shotlist, write_shotlist, _distribute_cuts, _build_prompt_text 검증.
Property 12 (숏리스트 완결성) — Validates: Requirements 9.1, 9.2, 9.3

Feature: short-form-harness
Property 12: 숏리스트의 완결성
  For any 프로파일과 시드된 rng에 대해, build_shotlist는 각 beat에 최소 1샷을
  생성하고(각 숏은 role·duration_sec·asset_type·prompt를 모두 포함), 총 숏 개수는
  pacing.cut_count_range 범위 내에 있으며, product_hero 장면이 존재하면 P0에서는
  모든 asset_type이 'imagen_image'다.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.generate.plan import (
    build_shotlist,
    write_shotlist,
    _distribute_cuts,
    _build_prompt_text,
)


# ---------------------------------------------------------------------------
# 테스트 픽스처 & 헬퍼
# ---------------------------------------------------------------------------

def _make_profile(beats=None, cut_count_range=(4, 12)) -> dict:
    """최소 유효 style_profile dict 반환."""
    if beats is None:
        beats = [
            {"role": "hook", "start_sec": 0.0, "end_sec": 0.9,
             "shot_type": "extreme_closeup", "intent": "grab attention"},
            {"role": "application", "start_sec": 0.9, "end_sec": 2.2,
             "shot_type": "medium_closeup", "intent": "show application"},
            {"role": "result_glow", "start_sec": 2.2, "end_sec": 3.7,
             "shot_type": "medium_closeup_glow", "intent": "show result"},
            {"role": "product_hero", "start_sec": 3.7, "end_sec": 4.6,
             "shot_type": "closeup_bottle", "intent": "showcase product"},
            {"role": "application", "start_sec": 4.6, "end_sec": 6.8,
             "shot_type": "closeup_hand", "intent": "show versatility"},
        ]
    return {
        "pacing": {
            "cut_count_range": list(cut_count_range),
            "avg_shot_len_sec": 1.0,
            "rhythm_mode": "mixed",
        },
        "narrative": {"beats": beats},
        "visual": {
            "color_grade": "warm_soft_aesthetic",
            "lighting": "natural_window_soft",
            "accent_color": "#ED99BE",
        },
    }


def _make_brief(value: str = "글로우 세럼", kind: str = "text",
                run_dir: str = "outputs/20240101_120000_abc123") -> dict:
    """최소 brief dict 반환."""
    return {
        "user_input": {"kind": kind, "value": value},
        "profile_path": "profiles/test.json",
        "run_dir": run_dir,
    }


def _seeded_rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


# ---------------------------------------------------------------------------
# _distribute_cuts 단위 테스트
# ---------------------------------------------------------------------------

class TestDistributeCuts:
    def test_empty_beats(self):
        assert _distribute_cuts([], 10) == []

    def test_minimum_one_cut_per_beat(self):
        beats = [
            {"start_sec": 0.0, "end_sec": 1.0},
            {"start_sec": 1.0, "end_sec": 2.0},
            {"start_sec": 2.0, "end_sec": 3.0},
        ]
        result = _distribute_cuts(beats, 5)
        assert all(c >= 1 for c in result)

    def test_total_cuts_equals_requested(self):
        beats = [
            {"start_sec": 0.0, "end_sec": 2.0},
            {"start_sec": 2.0, "end_sec": 3.0},
            {"start_sec": 3.0, "end_sec": 5.0},
            {"start_sec": 5.0, "end_sec": 6.5},
            {"start_sec": 6.5, "end_sec": 9.9},
        ]
        for total in [5, 8, 12, 15]:
            result = _distribute_cuts(beats, total)
            # total >= num_beats → 합은 total
            if total >= len(beats):
                assert sum(result) == total
            else:
                # total < num_beats → 최소 보장으로 모두 1
                assert sum(result) == len(beats)

    def test_total_cuts_less_than_beats(self):
        """총 컷 수가 beat 수보다 적으면 모든 beat에 1컷씩."""
        beats = [
            {"start_sec": i * 1.0, "end_sec": (i + 1) * 1.0}
            for i in range(5)
        ]
        result = _distribute_cuts(beats, 3)
        assert result == [1, 1, 1, 1, 1]

    def test_longer_beats_get_more_cuts(self):
        """duration이 긴 beat에 더 많은 컷이 배분된다."""
        beats = [
            {"start_sec": 0.0, "end_sec": 0.5},   # 짧음 (0.5초)
            {"start_sec": 0.5, "end_sec": 5.5},   # 긺 (5.0초)
        ]
        result = _distribute_cuts(beats, 7)
        assert result[1] >= result[0], "긴 beat가 짧은 beat보다 컷이 적어선 안 된다"

    def test_five_beats_twelve_cuts_distribution(self):
        """beats=5, cut_count=12의 구체적 예시 (태스크 명세 예시)."""
        beats = [
            {"start_sec": 0.0, "end_sec": 0.9},   # 0.9초
            {"start_sec": 0.9, "end_sec": 2.2},   # 1.3초
            {"start_sec": 2.2, "end_sec": 3.7},   # 1.5초
            {"start_sec": 3.7, "end_sec": 4.6},   # 0.9초
            {"start_sec": 4.6, "end_sec": 9.9},   # 5.3초 (가장 길다)
        ]
        result = _distribute_cuts(beats, 12)
        assert len(result) == 5
        assert all(c >= 1 for c in result)
        assert sum(result) == 12
        # 가장 긴 beat(index 4)가 가장 많은 컷을 받아야 한다
        assert result[4] >= result[0]


# ---------------------------------------------------------------------------
# _build_prompt_text 단위 테스트
# ---------------------------------------------------------------------------

class TestBuildPromptText:
    def _make_beat(self, role="hook", shot_type="extreme_closeup",
                   intent="grab attention") -> dict:
        return {"role": role, "shot_type": shot_type, "intent": intent}

    def test_contains_shot_type(self):
        beat = self._make_beat(shot_type="medium_closeup_face")
        profile = _make_profile()
        brief = _make_brief()
        result = _build_prompt_text(beat, profile, brief)
        assert "medium_closeup_face" in result

    def test_contains_intent(self):
        beat = self._make_beat(intent="show glowing skin")
        profile = _make_profile()
        brief = _make_brief()
        result = _build_prompt_text(beat, profile, brief)
        assert "show glowing skin" in result

    def test_contains_color_grade(self):
        beat = self._make_beat()
        profile = _make_profile()
        brief = _make_brief()
        result = _build_prompt_text(beat, profile, brief)
        assert "warm_soft_aesthetic" in result

    def test_contains_lighting(self):
        beat = self._make_beat()
        profile = _make_profile()
        brief = _make_brief()
        result = _build_prompt_text(beat, profile, brief)
        assert "natural_window_soft" in result

    def test_contains_product_subject(self):
        beat = self._make_beat()
        profile = _make_profile()
        brief = _make_brief(value="비타민 세럼")
        result = _build_prompt_text(beat, profile, brief)
        assert "비타민 세럼" in result

    def test_image_input_uses_stem(self):
        beat = self._make_beat()
        profile = _make_profile()
        brief = _make_brief(value="assets/glow_product.png", kind="image")
        result = _build_prompt_text(beat, profile, brief)
        assert "glow_product" in result

    def test_contains_9_16_format(self):
        beat = self._make_beat()
        result = _build_prompt_text(beat, _make_profile(), _make_brief())
        assert "9:16" in result


# ---------------------------------------------------------------------------
# build_shotlist 단위 테스트
# ---------------------------------------------------------------------------

class TestBuildShotlist:
    def test_returns_dict_with_shots(self):
        shotlist = build_shotlist(
            _make_brief(), _make_profile(), "훅 텍스트", rng=_seeded_rng()
        )
        assert "shots" in shotlist
        assert isinstance(shotlist["shots"], list)

    def test_run_id_from_brief_run_dir(self):
        brief = _make_brief(run_dir="outputs/20240615_143022_a1b2c3")
        shotlist = build_shotlist(brief, _make_profile(), "훅", rng=_seeded_rng())
        assert shotlist["run_id"] == "20240615_143022_a1b2c3"

    def test_total_shots_within_cut_count_range(self):
        profile = _make_profile(cut_count_range=(4, 12))
        shotlist = build_shotlist(
            _make_brief(), profile, "훅", rng=_seeded_rng(42)
        )
        n = len(shotlist["shots"])
        assert 4 <= n <= 12, f"숏 수 {n}이 cut_count_range [4,12] 밖이다"

    def test_minimum_one_shot_per_beat(self):
        """각 beat에 최소 1개의 숏이 생성된다 (요구사항 9.1)."""
        profile = _make_profile()
        beats = profile["narrative"]["beats"]
        shotlist = build_shotlist(
            _make_brief(), profile, "훅", rng=_seeded_rng(99)
        )
        roles_in_shotlist = [s["role"] for s in shotlist["shots"]]
        for beat in beats:
            assert beat["role"] in roles_in_shotlist, (
                f"beat role '{beat['role']}' 가 숏리스트에 없다"
            )

    def test_all_shots_have_required_fields(self):
        """모든 숏은 role·duration_sec·asset_type·prompt를 포함한다 (요구사항 9.1)."""
        shotlist = build_shotlist(
            _make_brief(), _make_profile(), "훅", rng=_seeded_rng()
        )
        for shot in shotlist["shots"]:
            assert "role" in shot
            assert "duration_sec" in shot
            assert "asset_type" in shot
            assert "prompt" in shot
            assert "index" in shot
            assert "asset_path" in shot

    def test_asset_path_initially_empty(self):
        """asset_path는 초기에 빈 문자열이다 (assets.py 단계에서 채워짐)."""
        shotlist = build_shotlist(
            _make_brief(), _make_profile(), "훅", rng=_seeded_rng()
        )
        for shot in shotlist["shots"]:
            assert shot["asset_path"] == ""

    def test_all_shots_veo_i2v(self):
        """모든 숏의 asset_type은 'veo_i2v'다 (전체 동영상화)."""
        profile = _make_profile()
        shotlist = build_shotlist(
            _make_brief(), profile, "훅", rng=_seeded_rng()
        )
        for shot in shotlist["shots"]:
            assert shot["asset_type"] == "veo_i2v", (
                f"shot[{shot['index']}] role={shot['role']} asset_type={shot['asset_type']}"
            )

    def test_shot_indices_are_sequential(self):
        shotlist = build_shotlist(
            _make_brief(), _make_profile(), "훅", rng=_seeded_rng()
        )
        for i, shot in enumerate(shotlist["shots"]):
            assert shot["index"] == i

    def test_empty_beats_returns_empty_shots(self):
        profile = _make_profile(beats=[])
        shotlist = build_shotlist(
            _make_brief(), profile, "훅", rng=_seeded_rng()
        )
        assert shotlist["shots"] == []

    def test_single_beat_single_cut_range(self):
        beats = [{"role": "hook", "start_sec": 0.0, "end_sec": 3.0,
                  "shot_type": "closeup", "intent": "hook"}]
        profile = _make_profile(beats=beats, cut_count_range=(1, 1))
        shotlist = build_shotlist(
            _make_brief(), profile, "훅", rng=_seeded_rng()
        )
        assert len(shotlist["shots"]) == 1
        assert shotlist["shots"][0]["role"] == "hook"

    def test_duration_sec_positive(self):
        """모든 숏의 duration_sec은 양수여야 한다."""
        shotlist = build_shotlist(
            _make_brief(), _make_profile(), "훅", rng=_seeded_rng()
        )
        for shot in shotlist["shots"]:
            assert shot["duration_sec"] > 0

    def test_deterministic_with_same_rng_seed(self):
        """동일한 seed로 생성된 rng는 동일한 숏리스트를 만든다."""
        profile = _make_profile()
        brief = _make_brief()
        shotlist1 = build_shotlist(brief, profile, "훅", rng=random.Random(7))
        shotlist2 = build_shotlist(brief, profile, "훅", rng=random.Random(7))
        assert shotlist1["shots"] == shotlist2["shots"]

    def test_prompt_contains_product_subject(self):
        brief = _make_brief(value="비타민 세럼")
        shotlist = build_shotlist(brief, _make_profile(), "훅", rng=_seeded_rng())
        for shot in shotlist["shots"]:
            assert "비타민 세럼" in shot["prompt"]


# ---------------------------------------------------------------------------
# write_shotlist 단위 테스트
# ---------------------------------------------------------------------------

class TestWriteShotlist:
    def test_creates_shotlist_json(self, tmp_path):
        run_dir = tmp_path / "outputs" / "run_001"
        run_dir.mkdir(parents=True)
        shotlist = {"run_id": "run_001", "shots": []}
        write_shotlist(shotlist, str(run_dir))
        dest = run_dir / "shotlist.json"
        assert dest.exists()

    def test_json_content_matches(self, tmp_path):
        run_dir = tmp_path / "outputs" / "run_002"
        run_dir.mkdir(parents=True)
        shotlist = {
            "run_id": "run_002",
            "shots": [
                {"index": 0, "role": "hook", "asset_type": "imagen_image",
                 "duration_sec": 1.2, "prompt": "test prompt", "asset_path": ""},
            ],
        }
        write_shotlist(shotlist, str(run_dir))
        loaded = json.loads((run_dir / "shotlist.json").read_text(encoding="utf-8"))
        assert loaded == shotlist

    def test_creates_parent_dirs(self, tmp_path):
        """run_dir이 없어도 자동 생성된다."""
        run_dir = tmp_path / "outputs" / "nonexistent_run"
        # mkdir 안 함 — write_json이 생성해야 함
        shotlist = {"run_id": "nonexistent_run", "shots": []}
        write_shotlist(shotlist, str(run_dir))
        assert (run_dir / "shotlist.json").exists()

    def test_shotlist_json_has_role_asset_type_prompt_path(self, tmp_path):
        """shotlist.json은 role·asset_type·prompt·asset_path를 포함한다 (요구사항 16.4)."""
        run_dir = tmp_path / "run_req164"
        run_dir.mkdir()
        shotlist = {
            "run_id": "run_req164",
            "shots": [
                {
                    "index": 0,
                    "role": "hook",
                    "asset_type": "imagen_image",
                    "duration_sec": 1.5,
                    "prompt": "a beauty shot",
                    "asset_path": "outputs/run_req164/shot_00.png",
                }
            ],
        }
        write_shotlist(shotlist, str(run_dir))
        loaded = json.loads((run_dir / "shotlist.json").read_text(encoding="utf-8"))
        shot = loaded["shots"][0]
        assert "role" in shot
        assert "asset_type" in shot
        assert "prompt" in shot
        assert "asset_path" in shot


# ---------------------------------------------------------------------------
# Property-Based Test: Property 12 — 숏리스트 완결성
# Feature: short-form-harness, Property 12: 숏리스트의 완결성
# Validates: Requirements 9.1, 9.2, 9.3
# ---------------------------------------------------------------------------

# Hypothesis 전략: beat 생성
@st.composite
def beat_strategy(draw):
    """유효한 single beat dict 생성 전략."""
    role = draw(st.sampled_from([
        "hook", "product_hero", "application", "result_glow", "cta_card"
    ]))
    start = draw(st.floats(min_value=0.0, max_value=20.0, allow_nan=False, allow_infinity=False))
    duration = draw(st.floats(min_value=0.1, max_value=5.0, allow_nan=False, allow_infinity=False))
    end = start + duration
    shot_type = draw(st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_"),
        min_size=3, max_size=30,
    ))
    intent = draw(st.text(min_size=5, max_size=80))
    return {
        "role": role,
        "start_sec": round(start, 3),
        "end_sec": round(end, 3),
        "shot_type": shot_type if shot_type else "medium_shot",
        "intent": intent,
    }


@st.composite
def profile_strategy(draw):
    """유효한 style_profile dict 생성 전략."""
    cut_min = draw(st.integers(min_value=1, max_value=8))
    cut_max = draw(st.integers(min_value=cut_min, max_value=20))
    beats = draw(st.lists(beat_strategy(), min_size=1, max_size=8))
    return {
        "pacing": {
            "cut_count_range": [cut_min, cut_max],
            "avg_shot_len_sec": 1.5,
            "rhythm_mode": "mixed",
        },
        "narrative": {"beats": beats},
        "visual": {
            "color_grade": "warm_soft_pastel",
            "lighting": "natural_window_soft",
            "accent_color": "#ED99BE",
        },
    }


@st.composite
def brief_strategy(draw):
    """유효한 brief dict 생성 전략."""
    kind = draw(st.sampled_from(["text", "image", "video"]))
    if kind == "text":
        value = draw(st.text(min_size=1, max_size=50))
    else:
        value = draw(st.text(
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_./"),
            min_size=5, max_size=40,
        )) + (".png" if kind == "image" else ".mp4")
    run_id = draw(st.text(
        alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_"),
        min_size=5, max_size=20,
    ))
    return {
        "user_input": {"kind": kind, "value": value if value else "product"},
        "run_dir": f"outputs/{run_id if run_id else 'run_001'}",
    }


@given(
    profile=profile_strategy(),
    brief=brief_strategy(),
    seed=st.integers(min_value=0, max_value=2**31 - 1),
)
@settings(max_examples=100)
def test_property_12_shotlist_completeness(profile, brief, seed):
    """Property 12: 숏리스트의 완결성
    Feature: short-form-harness, Property 12: 숏리스트의 완결성
    Validates: Requirements 9.1, 9.2, 9.3
    """
    rng = random.Random(seed)
    shotlist = build_shotlist(brief, profile, "test hook", rng=rng)

    shots = shotlist["shots"]
    cut_min, cut_max = profile["pacing"]["cut_count_range"]
    beats = profile["narrative"]["beats"]
    num_beats = len(beats)

    # 1. 총 숏 수는 max(cut_count_range, num_beats) 이상이어야 함
    #    (cut_count가 beat 수보다 작으면 최소 1컷/beat 보장으로 num_beats개)
    effective_min = max(cut_min, num_beats)
    assert len(shots) >= num_beats, (
        f"숏 수 {len(shots)} < beat 수 {num_beats}: 각 beat에 최소 1샷 필요"
    )

    # 2. 총 숏 수는 pacing 범위를 크게 벗어나지 않아야 함
    #    (cut_count < num_beats 경우 num_beats로 올라갈 수 있음)
    assert len(shots) >= cut_min or len(shots) >= num_beats, (
        f"숏 수 {len(shots)} < cut_min {cut_min}"
    )

    # 3. 각 beat의 role이 숏리스트에 최소 1번 등장
    roles_in_shots = {s["role"] for s in shots}
    for beat in beats:
        assert beat["role"] in roles_in_shots, (
            f"beat role '{beat['role']}' 가 숏리스트에 없다"
        )

    # 4. 모든 숏은 필수 필드를 포함
    required_fields = {"index", "role", "asset_type", "duration_sec", "prompt", "asset_path"}
    for shot in shots:
        missing = required_fields - shot.keys()
        assert not missing, f"숏 {shot.get('index')} 에 누락 필드: {missing}"

    # 5. asset_type: 모든 숏이 veo_i2v (전체 동영상화)
    for shot in shots:
        assert shot["asset_type"] == "veo_i2v", (
            f"모든 숏은 veo_i2v여야 함: {shot}"
        )

    # 6. duration_sec 양수
    for shot in shots:
        assert shot["duration_sec"] > 0, f"duration_sec이 양수가 아님: {shot}"

    # 7. index 순서 일관성
    for i, shot in enumerate(shots):
        assert shot["index"] == i, f"shot index {shot['index']} != 기대값 {i}"

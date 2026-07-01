"""
tests/test_compose.py — compose.py 단위 테스트

select_music, adjust_durations, compose_video (모킹) 등을 검증한다.
moviepy/ffmpeg를 직접 호출하지 않도록 compose_video 핵심 경로는 모킹한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.common.exceptions import HarnessError
from src.generate.compose import (
    select_music,
    get_music_duration,
    adjust_durations,
    _strip_emoji,
    _compute_caption_position,
    _TARGET_WIDTH,
    _TARGET_HEIGHT,
)


# ---------------------------------------------------------------------------
# 픽스처 헬퍼
# ---------------------------------------------------------------------------

def _write_index(music_dir: Path, tracks: list[dict]) -> None:
    """임시 디렉터리에 index.json을 작성한다."""
    music_dir.mkdir(parents=True, exist_ok=True)
    (music_dir / "index.json").write_text(
        json.dumps(tracks, ensure_ascii=False), encoding="utf-8"
    )


def _make_profile(
    *,
    duration_range: list = None,
    music_mood: str = "upbeat_light_kpop_inspired",
    music_start_sec: float = 0.0,
    target_lufs: float = -23.0,
    has_voiceover: bool = False,
    caption_slots: list = None,
    overlay: dict = None,
) -> dict:
    return {
        "format": {
            "aspect_ratio": "9:16",
            "resolution": "576x1024",
            "fps": 30.0,
            "duration_sec_range": duration_range or [10.0, 15.0],
        },
        "audio": {
            "music_mood": music_mood,
            "music_start_sec": music_start_sec,
            "target_lufs": target_lufs,
            "has_voiceover": has_voiceover,
        },
        "captions": {"slots": caption_slots or []},
        "overlay": overlay or {"platform_watermark": "placeholder", "handle_position": "left_mid", "end_card": "none"},
    }


def _make_shotlist(n: int = 3, duration_sec: float = 2.0, asset_path: str = "") -> dict:
    return {
        "run_id": "test_run_001",
        "shots": [
            {
                "index": i,
                "role": "application",
                "asset_type": "imagen_image",
                "duration_sec": duration_sec,
                "prompt": f"prompt {i}",
                "asset_path": asset_path,
            }
            for i in range(n)
        ],
    }


# ---------------------------------------------------------------------------
# select_music 테스트
# ---------------------------------------------------------------------------

class TestSelectMusic:
    def test_returns_path_under_music_dir(self, tmp_path):
        """select_music은 music_dir 하위 경로를 반환한다 (Property 14)."""
        music_dir = tmp_path / "music"
        _write_index(music_dir, [
            {"file": "track1.wav", "mood": "upbeat_light_kpop_inspired"},
        ])
        result = select_music("upbeat_light", str(music_dir))
        assert str(music_dir) in result

    def test_exact_mood_match_preferred(self, tmp_path):
        """mood 단어 교집합이 최대인 트랙이 선택된다 (Property 14)."""
        music_dir = tmp_path / "music"
        _write_index(music_dir, [
            {"file": "calm.wav", "mood": "calm_dreamy_ambient"},
            {"file": "upbeat.wav", "mood": "upbeat_light_kpop_inspired"},
            {"file": "soft.wav", "mood": "soft_upbeat_aesthetic"},
        ])
        result = select_music("upbeat_light_kpop_inspired", str(music_dir))
        assert "upbeat.wav" in result

    def test_partial_mood_match(self, tmp_path):
        """부분 매칭도 동작한다 — 가장 많은 단어가 겹치는 트랙 선택."""
        music_dir = tmp_path / "music"
        _write_index(music_dir, [
            {"file": "a.wav", "mood": "calm_ambient"},
            {"file": "b.wav", "mood": "upbeat_kpop"},
        ])
        result = select_music("upbeat_inspired", str(music_dir))
        assert "b.wav" in result

    def test_fallback_to_first_track_no_match(self, tmp_path):
        """매칭되는 단어가 없으면 첫 번째 트랙을 반환한다."""
        music_dir = tmp_path / "music"
        _write_index(music_dir, [
            {"file": "first.wav", "mood": "zzz_xyz"},
            {"file": "second.wav", "mood": "abc_def"},
        ])
        result = select_music("completely_different_mood", str(music_dir))
        assert "first.wav" in result

    def test_raises_harness_error_when_index_missing(self, tmp_path):
        """index.json이 없으면 HarnessError가 raise된다 (요구사항 11.5)."""
        music_dir = tmp_path / "no_music"
        music_dir.mkdir()
        with pytest.raises(HarnessError):
            select_music("upbeat", str(music_dir))

    def test_raises_harness_error_when_tracks_empty(self, tmp_path):
        """트랙 목록이 비어 있으면 HarnessError가 raise된다."""
        music_dir = tmp_path / "music"
        _write_index(music_dir, [])
        with pytest.raises(HarnessError):
            select_music("upbeat", str(music_dir))

    def test_real_index_json_exists(self):
        """assets/music/index.json 파일이 존재한다."""
        index_path = Path("assets/music/index.json")
        assert index_path.exists(), f"assets/music/index.json이 없습니다"

    def test_real_index_json_is_valid(self):
        """assets/music/index.json은 유효한 JSON 배열이다."""
        index_path = Path("assets/music/index.json")
        if not index_path.exists():
            pytest.skip("assets/music/index.json이 없음")
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) > 0

    def test_real_select_music_returns_assets_music_path(self):
        """실제 assets/music/index.json으로 select_music이 assets/music/ 경로를 반환한다."""
        if not Path("assets/music/index.json").exists():
            pytest.skip("assets/music/index.json이 없음")
        result = select_music("upbeat_light_kpop_inspired")
        assert result.startswith("assets/music") or "assets/music" in result


# ---------------------------------------------------------------------------
# get_music_duration 테스트
# ---------------------------------------------------------------------------

def _write_silent_wav(path: Path, duration_sec: float, sample_rate: int = 22050) -> None:
    """지정된 길이의 무음 WAV 파일을 작성한다 (get_music_duration 테스트용)."""
    import wave
    import struct as _struct

    n_frames = int(duration_sec * sample_rate)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(_struct.pack("<h", 0) * n_frames)


class TestGetMusicDuration:
    def test_returns_track_duration_without_offset(self, tmp_path):
        """music_start_sec=0이면 트랙 전체 길이를 반환한다."""
        music_dir = tmp_path / "music"
        _write_index(music_dir, [{"file": "track.wav", "mood": "upbeat_light"}])
        _write_silent_wav(music_dir / "track.wav", duration_sec=10.7)

        result = get_music_duration("upbeat_light", music_start_sec=0.0, music_dir=str(music_dir))
        assert result is not None
        assert abs(result - 10.7) < 0.1

    def test_subtracts_music_start_sec_offset(self, tmp_path):
        """music_start_sec만큼 트랙 재생 가능 길이에서 제외된다."""
        music_dir = tmp_path / "music"
        _write_index(music_dir, [{"file": "track.wav", "mood": "upbeat_light"}])
        _write_silent_wav(music_dir / "track.wav", duration_sec=10.7)

        result = get_music_duration("upbeat_light", music_start_sec=0.7, music_dir=str(music_dir))
        assert result is not None
        assert abs(result - 10.0) < 0.1

    def test_returns_none_when_index_missing(self, tmp_path):
        """index.json이 없으면 None을 반환한다 (예외를 raise하지 않음)."""
        music_dir = tmp_path / "no_music"
        music_dir.mkdir()
        result = get_music_duration("upbeat_light", music_dir=str(music_dir))
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path):
        """index.json은 있지만 실제 파일이 없으면 None을 반환한다."""
        music_dir = tmp_path / "music"
        _write_index(music_dir, [{"file": "missing.wav", "mood": "upbeat_light"}])
        result = get_music_duration("upbeat_light", music_dir=str(music_dir))
        assert result is None

    def test_real_ref1_bgm_duration(self):
        """실제 assets/music/ref1_bgm.mp3 길이가 약 10.7초임을 확인한다."""
        if not Path("assets/music/ref1_bgm.mp3").exists():
            pytest.skip("assets/music/ref1_bgm.mp3가 없음")
        result = get_music_duration("upbeat_light_kpop_inspired", music_start_sec=0.0464)
        assert result is not None
        assert 9.0 < result < 11.0


# ---------------------------------------------------------------------------
# adjust_durations 테스트 (순수 함수)
# ---------------------------------------------------------------------------

class TestAdjustDurations:
    def test_no_adjustment_needed(self):
        """범위 내 총 duration은 변경되지 않는다."""
        durations = [3.0, 4.0, 3.0]  # total = 10.0, within [10, 15]
        result = adjust_durations(durations, 10.0, 15.0)
        assert abs(sum(result) - 10.0) < 0.01

    def test_extends_last_clip_when_too_short(self):
        """총 duration이 min보다 짧으면 마지막 클립이 연장된다."""
        durations = [2.0, 2.0, 2.0]  # total = 6.0 < 10.0
        result = adjust_durations(durations, 10.0, 15.0)
        assert abs(sum(result) - 10.0) < 0.01
        # 마지막 클립이 늘어났어야 함
        assert result[-1] > durations[-1]

    def test_truncates_when_too_long(self):
        """총 duration이 max보다 길면 max 이하로 줄어든다."""
        durations = [5.0, 5.0, 5.0, 5.0]  # total = 20.0 > 15.0
        result = adjust_durations(durations, 10.0, 15.0)
        assert sum(result) <= 15.0 + 0.01

    def test_empty_list(self):
        """빈 리스트는 빈 리스트를 반환한다."""
        assert adjust_durations([], 10.0, 15.0) == []

    def test_single_clip_too_short(self):
        """단일 클립이 너무 짧으면 duration_min으로 늘어난다."""
        result = adjust_durations([3.0], 10.0, 15.0)
        assert abs(sum(result) - 10.0) < 0.01

    def test_single_clip_too_long(self):
        """단일 클립이 너무 길면 duration_max로 줄어든다."""
        result = adjust_durations([30.0], 10.0, 15.0)
        assert sum(result) <= 15.0 + 0.01

    def test_exactly_at_min(self):
        """총 duration이 min과 정확히 같으면 그대로 반환한다."""
        durations = [5.0, 5.0]
        result = adjust_durations(durations, 10.0, 15.0)
        assert abs(sum(result) - 10.0) < 0.01

    def test_exactly_at_max(self):
        """총 duration이 max와 정확히 같으면 그대로 반환한다."""
        durations = [5.0, 5.0, 5.0]
        result = adjust_durations(durations, 10.0, 15.0)
        assert abs(sum(result) - 15.0) < 0.01

    def test_output_length_not_longer_than_input(self):
        """출력 리스트 길이는 입력보다 길지 않다."""
        durations = [3.0, 3.0, 3.0]
        result = adjust_durations(durations, 10.0, 15.0)
        assert len(result) <= len(durations)

    def test_all_durations_non_negative(self):
        """조정 후 모든 duration은 0 이상이다."""
        durations = [1.0, 1.0, 20.0]
        result = adjust_durations(durations, 10.0, 15.0)
        assert all(d >= 0 for d in result)


# ---------------------------------------------------------------------------
# _strip_emoji 테스트
# ---------------------------------------------------------------------------

class TestStripEmoji:
    def test_removes_sparkle(self):
        assert "✨" not in _strip_emoji("테스트 ✨ 텍스트")

    def test_preserves_korean(self):
        result = _strip_emoji("피부가 좋아졌어요 ✨")
        assert "피부가 좋아졌어요" in result

    def test_preserves_english(self):
        result = _strip_emoji("hello world 💖")
        assert "hello world" in result

    def test_empty_string(self):
        assert _strip_emoji("") == ""

    def test_only_emoji(self):
        result = _strip_emoji("✨💖🌟")
        assert result.strip() == ""


# ---------------------------------------------------------------------------
# _compute_caption_position 테스트
# ---------------------------------------------------------------------------

class TestComputeCaptionPosition:
    def test_top_center(self):
        h, v = _compute_caption_position("top_center")
        assert h == "center"
        assert v < _TARGET_HEIGHT * 0.2  # 상단

    def test_bottom_center(self):
        h, v = _compute_caption_position("bottom_center")
        assert h == "center"
        assert v > _TARGET_HEIGHT * 0.7  # 하단

    def test_lower_third(self):
        h, v = _compute_caption_position("lower_third")
        assert h == "center"
        assert v > _TARGET_HEIGHT * 0.6  # 하단부

    def test_unknown_anchor_returns_center(self):
        h, v = _compute_caption_position("unknown_anchor")
        assert h == "center"


# ---------------------------------------------------------------------------
# compose_video 모킹 테스트
# ---------------------------------------------------------------------------

class TestComposeVideoMocked:
    """moviepy의 write_videofile 및 CompositeVideoClip을 모킹해
    실제 영상을 생성하지 않고 compose_video 로직을 검증한다."""

    def _make_mock_clip(self, duration=10.0):
        clip = MagicMock()
        clip.duration = duration
        clip.w = _TARGET_WIDTH
        clip.h = _TARGET_HEIGHT
        clip.resize.return_value = clip
        clip.crop.return_value = clip
        clip.set_duration.return_value = clip
        clip.set_audio.return_value = clip
        clip.write_videofile.return_value = None
        return clip

    @patch("src.generate.compose.select_music")
    @patch("src.generate.compose.CompositeVideoClip" if False else "src.generate.compose._build_audio_track")
    def test_compose_creates_output_file(self, mock_audio, mock_select, tmp_path):
        """compose_video는 final.mp4 경로를 반환한다."""
        mock_select.return_value = "assets/music/silence_upbeat.wav"
        mock_audio.return_value = None

        profile = _make_profile()
        shotlist = _make_shotlist(2, duration_sec=5.5)

        # moviepy 전체를 모킹
        mock_clip = self._make_mock_clip(11.0)

        with patch.dict("sys.modules", {
            "moviepy": MagicMock(),
            "moviepy.editor": MagicMock(
                ImageClip=MagicMock(return_value=mock_clip),
                VideoFileClip=MagicMock(return_value=mock_clip),
                AudioFileClip=MagicMock(return_value=MagicMock(duration=5.0)),
                CompositeVideoClip=MagicMock(return_value=mock_clip),
                concatenate_videoclips=MagicMock(return_value=mock_clip),
                ColorClip=MagicMock(return_value=mock_clip),
                TextClip=MagicMock(return_value=mock_clip),
            ),
        }):
            import importlib
            import src.generate.compose as compose_mod
            importlib.reload(compose_mod)

            result = compose_mod.compose_video(shotlist, profile, str(tmp_path))
            assert "final.mp4" in result

    def test_compose_returns_run_dir_path(self, tmp_path):
        """반환된 경로는 run_dir/final.mp4 형태여야 한다."""
        profile = _make_profile()
        shotlist = _make_shotlist(2, duration_sec=5.5)

        mock_clip = self._make_mock_clip(11.0)

        with patch.dict("sys.modules", {
            "moviepy": MagicMock(),
            "moviepy.editor": MagicMock(
                ImageClip=MagicMock(return_value=mock_clip),
                VideoFileClip=MagicMock(return_value=mock_clip),
                AudioFileClip=MagicMock(return_value=MagicMock(duration=5.0)),
                CompositeVideoClip=MagicMock(return_value=mock_clip),
                concatenate_videoclips=MagicMock(return_value=mock_clip),
                ColorClip=MagicMock(return_value=mock_clip),
                TextClip=MagicMock(return_value=mock_clip),
            ),
        }):
            import importlib
            import src.generate.compose as compose_mod
            importlib.reload(compose_mod)

            result = compose_mod.compose_video(shotlist, profile, str(tmp_path))
            assert str(tmp_path) in result
            assert result.endswith("final.mp4")


# ---------------------------------------------------------------------------
# 격리 불변식 테스트 (요구사항 13.2, 13.3)
# ---------------------------------------------------------------------------

class TestIsolationInvariant:
    def test_compose_does_not_import_analyze(self):
        """compose.py 소스코드에 src.analyze import가 없다 (요구사항 13.2)."""
        import inspect
        import src.generate.compose as compose_mod
        source = inspect.getsource(compose_mod)
        assert "src.analyze" not in source, "compose.py에 src.analyze import가 발견됨"

    def test_compose_does_not_reference_refs_dir(self):
        """compose.py 소스코드에 refs/ 경로가 없다 (요구사항 11.9, 13.3)."""
        import inspect
        import src.generate.compose as compose_mod
        source = inspect.getsource(compose_mod)
        assert "refs/" not in source, "compose.py에 refs/ 경로 참조가 발견됨"

    def test_compose_does_not_import_google_sdk_directly(self):
        """compose.py는 Google SDK를 직접 import하지 않는다 (요구사항 13.5)."""
        import inspect
        import src.generate.compose as compose_mod
        source = inspect.getsource(compose_mod)
        assert "google.generativeai" not in source
        assert "from google import genai" not in source
        assert "google.genai" not in source

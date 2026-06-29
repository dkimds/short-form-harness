"""
src/generate/compose.py — moviepy/ffmpeg 영상 합성 (P0)

요구사항: 11.1~11.9, 17.2, 17.3

핵심 설계 원칙:
- shotlist 순서대로 클립 배열
- 9:16 (576×1024) 해상도 강제
- duration_sec_range 내 길이 조정
- captions.slots 대로 자막 오버레이 (P0: 이모지 제거)
- assets/music/에서 mood 매칭 트랙 선택, music_start_sec·target_lufs 적용
- overlay(핸들·엔드카드) placeholder 렌더링
- 레퍼런스 프레임/오디오 직접 사용 금지 (요구사항 11.9)
- src/analyze/ import 금지 (요구사항 13.2)
"""

from __future__ import annotations

import json
import logging
import os
import re
import warnings
from pathlib import Path

from src.common.exceptions import HarnessError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_TARGET_WIDTH = 576
_TARGET_HEIGHT = 1024
_TARGET_FPS = 30
_DEFAULT_MUSIC_DIR = "assets/music"

# LUFS 정규화: music at -14 LUFS 가정, target_lufs로 조정
_ASSUMED_MUSIC_LUFS = -14.0


# ---------------------------------------------------------------------------
# 이모지 제거 헬퍼 (P0: PIL 기본 폰트 호환)
# ---------------------------------------------------------------------------

def _strip_emoji(text: str) -> str:
    """이모지 및 특수 심볼 범위 문자를 제거해 PIL 기본 폰트 호환 문자열을 반환한다."""
    result = []
    for ch in text:
        cp = ord(ch)
        if cp >= 0x1F000:
            continue
        if 0x2600 <= cp <= 0x27BF:
            continue
        result.append(ch)
    return "".join(result)


# ---------------------------------------------------------------------------
# 음악 선택 (퍼블릭 API)
# ---------------------------------------------------------------------------

def select_music(music_mood: str, music_dir: str = _DEFAULT_MUSIC_DIR) -> str:
    """mood와 가장 일치하는 라이선스 안전 로컬 트랙을 선택한다.

    assets/music/index.json을 읽어 mood 단어 교집합이 최대인 트랙을 반환한다.
    동점이면 index.json의 첫 번째 순위 트랙을 사용한다.

    Args:
        music_mood: 요청 mood 문자열 (예: "upbeat_light_kpop_inspired")
        music_dir: music 디렉터리 경로 (기본값: "assets/music")

    Returns:
        선택된 트랙의 전체 경로 문자열

    Raises:
        HarnessError: index.json이 없거나 트랙 목록이 비어 있을 때
    """
    index_path = Path(music_dir) / "index.json"
    if not index_path.exists():
        raise HarnessError(
            f"음악 인덱스 파일을 찾을 수 없습니다: {index_path}\n"
            "  해결: assets/music/index.json 파일을 생성하고 트랙 메타데이터를 추가하세요."
        )

    try:
        with open(index_path, encoding="utf-8") as f:
            tracks: list[dict] = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise HarnessError(
            f"음악 인덱스 파일을 읽을 수 없습니다: {index_path}\n  원인: {exc}"
        ) from exc

    if not tracks:
        raise HarnessError(
            f"음악 인덱스가 비어 있습니다: {index_path}\n"
            "  해결: 트랙을 1개 이상 추가하세요."
        )

    request_words = set(music_mood.lower().split("_"))

    best_track = None
    best_score = -1

    for track in tracks:
        track_mood = track.get("mood", "")
        track_words = set(track_mood.lower().split("_"))
        score = len(request_words & track_words)
        if score > best_score:
            best_score = score
            best_track = track

    if best_track is None:
        best_track = tracks[0]

    file_name = best_track["file"]
    full_path = str(Path(music_dir) / file_name)
    logger.info("[compose] 음악 선택: %s (mood=%s, score=%d)", file_name, best_track.get("mood"), best_score)
    return full_path


# ---------------------------------------------------------------------------
# 순수 헬퍼 함수 (duration 조정 로직 — 테스트 가능한 순수 함수)
# ---------------------------------------------------------------------------

def adjust_durations(
    shot_durations: list[float],
    duration_min: float,
    duration_max: float,
) -> list[float]:
    """숏 duration 리스트를 [duration_min, duration_max] 범위에 맞게 조정한다.

    - 총 duration < duration_min: 마지막 클립을 늘려 채운다
    - 총 duration > duration_max: 뒤에서부터 클립을 잘라 맞춘다
    - 범위 내이면 그대로 반환한다

    Args:
        shot_durations: 각 숏의 duration_sec 리스트
        duration_min: 최소 재생 시간 (초)
        duration_max: 최대 재생 시간 (초)

    Returns:
        조정된 duration 리스트 (입력과 같은 길이 또는 짧아질 수 있음)
    """
    if not shot_durations:
        return []

    durations = list(shot_durations)
    total = sum(durations)

    # 너무 짧으면 마지막 클립 연장
    if total < duration_min:
        deficit = duration_min - total
        durations[-1] += deficit
        return durations

    # 너무 길면 뒤에서부터 잘라냄
    if total > duration_max:
        surplus = total - duration_max
        # 뒤에서부터 클립을 줄이거나 제거
        for i in range(len(durations) - 1, -1, -1):
            if surplus <= 0:
                break
            cut = min(durations[i], surplus)
            durations[i] -= cut
            surplus -= cut

        # duration이 0 이하인 클립 제거
        durations = [d for d in durations if d > 0]

    return durations


def _compute_caption_position(
    anchor: str, video_height: int = _TARGET_HEIGHT
) -> tuple[str, int | float]:
    """anchor 문자열을 moviepy position으로 변환한다.

    Returns:
        (horizontal, vertical) 위치 튜플
    """
    if anchor == "top_center":
        return ("center", int(video_height * 0.05))
    elif anchor == "bottom_center":
        return ("center", int(video_height * 0.88))
    elif anchor == "lower_third":
        return ("center", int(video_height * 0.78))
    else:
        return ("center", int(video_height * 0.50))


# ---------------------------------------------------------------------------
# 자막 렌더링 헬퍼
# ---------------------------------------------------------------------------

def _make_caption_clip(
    slot: dict,
    text: str,
    video_width: int = _TARGET_WIDTH,
    video_height: int = _TARGET_HEIGHT,
) -> object | None:
    """단일 자막 슬롯에 대한 moviepy TextClip을 생성한다.

    Args:
        slot: captions.slots 항목 (anchor, appear_sec, duration_sec 포함)
        text: 렌더링할 텍스트 (이모지 제거 완료 상태여야 함)
        video_width: 영상 너비
        video_height: 영상 높이

    Returns:
        설정된 TextClip 또는 None (ImageMagick 없음 등 실패 시)
    """
    try:
        from moviepy import TextClip  # type: ignore[import]
    except ImportError:
        logger.warning("[compose] moviepy를 import할 수 없습니다 — 자막 건너뜀")
        return None

    text_clean = _strip_emoji(text).strip()
    if not text_clean:
        return None

    anchor = slot.get("anchor", "bottom_center")
    appear_sec = float(slot.get("appear_sec", 0.0))
    duration_sec = float(slot.get("duration_sec", 3.0))
    size_pct = float(slot.get("size_pct", 4.0))

    # 폰트 크기: video_height * size_pct / 100
    font_size = max(16, int(video_height * size_pct / 100))

    # 텍스트 너비: 영상의 90%
    text_width = int(video_width * 0.9)

    try:
        clip = TextClip(
            text=text_clean,
            font_size=font_size,
            color="white",
            stroke_color="black",
            stroke_width=1,
            size=(text_width, None),
        )
        h_pos, v_pos = _compute_caption_position(anchor, video_height)
        clip = clip.with_position((h_pos, v_pos)).with_start(appear_sec).with_duration(duration_sec)
        return clip
    except Exception as exc:
        logger.warning("[compose] TextClip 생성 실패 — 자막 건너뜀: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 오버레이 헬퍼
# ---------------------------------------------------------------------------

def _make_overlay_clips(
    overlay: dict,
    video_duration: float,
    video_width: int = _TARGET_WIDTH,
    video_height: int = _TARGET_HEIGHT,
) -> list:
    """overlay 설정에 따라 핸들 watermark 및 엔드카드 클립 리스트를 반환한다.

    Args:
        overlay: profile.overlay dict (handle_position, end_card 등)
        video_duration: 전체 영상 재생 시간 (초)
        video_width: 영상 너비
        video_height: 영상 높이

    Returns:
        moviepy 클립 리스트 (빈 리스트 가능)
    """
    clips = []

    try:
        from moviepy import TextClip  # type: ignore[import]
    except ImportError:
        logger.warning("[compose] moviepy를 import할 수 없습니다 — overlay 건너뜀")
        return clips

    # 핸들 placeholder
    handle_position = overlay.get("handle_position", "left_mid")
    watermark_text = _strip_emoji("@handle")
    try:
        handle_clip = TextClip(
            text=watermark_text,
            font_size=18,
            color="white",
            stroke_color="black",
            stroke_width=1,
        )
        if "left" in handle_position:
            h_pos: str | int = int(video_width * 0.05)
        elif "right" in handle_position:
            h_pos = int(video_width * 0.7)
        else:
            h_pos = "center"

        if "top" in handle_position:
            v_pos: str | int = int(video_height * 0.05)
        elif "mid" in handle_position or "center" in handle_position:
            v_pos = int(video_height * 0.5)
        else:
            v_pos = int(video_height * 0.85)

        handle_clip = handle_clip.with_position((h_pos, v_pos)).with_duration(video_duration)
        clips.append(handle_clip)
    except Exception as exc:
        logger.warning("[compose] 핸들 overlay 생성 실패: %s", exc)

    # 엔드카드
    end_card = overlay.get("end_card", "none")
    if end_card != "none" and video_duration > 2.0:
        end_start = max(0.0, video_duration - 2.0)
        try:
            end_text = _strip_emoji("Follow for more")
            end_clip = TextClip(
                text=end_text,
                font_size=24,
                color="white",
                stroke_color="black",
                stroke_width=1,
            )
            end_clip = (
                end_clip
                .with_position(("center", int(video_height * 0.85)))
                .with_start(end_start)
                .with_duration(2.0)
            )
            clips.append(end_clip)
        except Exception as exc:
            logger.warning("[compose] 엔드카드 생성 실패: %s", exc)

    return clips


# ---------------------------------------------------------------------------
# 메인 합성 함수 (퍼블릭 API)
# ---------------------------------------------------------------------------

def compose_video(shotlist: dict, profile: dict, run_dir: str) -> str:
    """shotlist의 클립들을 조립해 final.mp4를 생성하고 경로를 반환한다.

    동작 순서:
    1. shots의 asset_path에서 ImageClip/VideoFileClip 로드 (실패 시 ColorClip 폴백)
    2. duration_sec_range에 맞게 클립 길이 조정
    3. 9:16 (576×1024) 해상도로 resize/crop
    4. 자막(captions.slots) 오버레이 (이모지 제거)
    5. 배경 음악 믹싱 (music_mood 선택, music_start_sec 오프셋, LUFS 정규화)
    6. voiceover 믹싱 (run_dir/voiceover.wav 존재 시)
    7. overlay 합성 (핸들 watermark, 엔드카드)
    8. final.mp4 저장

    Args:
        shotlist: build_shotlist()가 반환한 숏리스트 dict
        profile: style_profile dict
        run_dir: outputs/<run_id>/ 경로

    Returns:
        저장된 final.mp4의 절대 경로 문자열
    """
    try:
        from moviepy import (  # type: ignore[import]
            ImageClip,
            VideoFileClip,
            AudioFileClip,
            CompositeVideoClip,
            concatenate_videoclips,
            ColorClip,
        )
    except ImportError as exc:
        raise HarnessError(
            f"moviepy를 import할 수 없습니다: {exc}\n"
            "  해결: pip install moviepy 를 실행하세요."
        ) from exc

    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    output_path = run_path / "final.mp4"

    # --- 프로파일 값 추출 ---
    fmt = profile.get("format", {})
    duration_range = fmt.get("duration_sec_range", [10.0, 15.0])
    duration_min = float(duration_range[0])
    duration_max = float(duration_range[1])

    audio_cfg = profile.get("audio", {})
    music_mood = audio_cfg.get("music_mood", "upbeat")
    music_start_sec = float(audio_cfg.get("music_start_sec", 0.0))
    target_lufs = float(audio_cfg.get("target_lufs", -23.0))

    captions_cfg = profile.get("captions", {})
    caption_slots = captions_cfg.get("slots", [])

    overlay_cfg = profile.get("overlay", {})

    shots = shotlist.get("shots", [])

    # --- 1. 클립 로드 (실제 duration 확인) ---
    # VideoFileClip(mp4)은 자체 duration을 쓰므로 먼저 실제 duration을 파악
    real_durations = []
    for shot in shots:
        asset_path = shot.get("asset_path", "")
        ext = Path(asset_path).suffix.lower() if asset_path else ""
        if ext in (".mp4", ".mov", ".avi", ".webm") and asset_path and Path(asset_path).exists():
            try:
                probe_clip = VideoFileClip(asset_path)
                real_durations.append(float(probe_clip.duration))
                probe_clip.close()
            except Exception:
                real_durations.append(float(shot.get("duration_sec", 1.0)))
        else:
            real_durations.append(float(shot.get("duration_sec", 1.0)))

    adjusted_durations = adjust_durations(real_durations, duration_min, duration_max)

    # adjusted_durations might be shorter if clips were trimmed to 0
    n_clips = len(adjusted_durations)

    base_clips = []
    for i, shot in enumerate(shots[:n_clips]):
        dur = adjusted_durations[i]
        if dur <= 0:
            continue
        asset_path = shot.get("asset_path", "")
        ext = Path(asset_path).suffix.lower() if asset_path else ""
        clip = _load_clip(asset_path, dur, ImageClip, VideoFileClip, ColorClip)
        # Resize to 9:16
        clip = _resize_clip(clip, _TARGET_WIDTH, _TARGET_HEIGHT)
        # VideoFileClip은 자체 duration 유지, ImageClip/ColorClip만 shotlist duration 적용
        if ext not in (".mp4", ".mov", ".avi", ".webm"):
            clip = clip.with_duration(dur)
        base_clips.append(clip)

    if not base_clips:
        logger.warning("[compose] 유효한 클립이 없습니다 — 단색 배경으로 대체합니다.")
        base_clips = [
            ColorClip((_TARGET_WIDTH, _TARGET_HEIGHT), color=(30, 30, 30))
            .with_duration(duration_min)
        ]

    # --- 2. 클립 이어붙이기 ---
    try:
        video = concatenate_videoclips(base_clips)
    except Exception as exc:
        logger.warning("[compose] concatenate 실패 — 첫 클립만 사용: %s", exc)
        video = base_clips[0]

    total_duration = video.duration

    # --- 3. 자막 오버레이 ---
    caption_clips = _build_caption_clips(caption_slots, total_duration)
    all_layers = [video] + caption_clips

    # --- 4. overlay (핸들, 엔드카드) ---
    overlay_clips = _make_overlay_clips(overlay_cfg, total_duration)
    all_layers += overlay_clips

    # 합성
    try:
        final_video = CompositeVideoClip(all_layers, size=(_TARGET_WIDTH, _TARGET_HEIGHT))
        final_video = final_video.with_duration(total_duration)
    except Exception as exc:
        logger.warning("[compose] CompositeVideoClip 실패 — 베이스 영상만 사용: %s", exc)
        final_video = video

    # --- 5. 음악 믹싱 ---
    audio_track = _build_audio_track(
        music_mood=music_mood,
        music_start_sec=music_start_sec,
        target_lufs=target_lufs,
        video_duration=total_duration,
        run_dir=run_dir,
        AudioFileClip=AudioFileClip,
    )
    if audio_track is not None:
        try:
            final_video = final_video.with_audio(audio_track)
        except Exception as exc:
            logger.warning("[compose] 오디오 설정 실패: %s", exc)

    # --- 6. 저장 ---
    logger.info("[compose] final.mp4 인코딩 시작: %s", output_path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            final_video.write_videofile(
                str(output_path),
                fps=_TARGET_FPS,
                codec="libx264",
                audio_codec="aac",
                logger=None,
            )
    except Exception as exc:
        logger.error("[compose] write_videofile 실패: %s", exc)
        raise HarnessError(
            f"영상 인코딩에 실패했습니다: {exc}\n"
            "  해결: ffmpeg이 설치되어 있는지 확인하세요."
        ) from exc

    logger.info("[compose] final.mp4 저장 완료: %s", output_path)
    return str(output_path.resolve())


# ---------------------------------------------------------------------------
# 내부 헬퍼 함수들
# ---------------------------------------------------------------------------

def _load_clip(
    asset_path: str,
    duration: float,
    ImageClip: type,
    VideoFileClip: type,
    ColorClip: type,
) -> object:
    """asset_path로부터 moviepy 클립을 로드한다. 실패 시 ColorClip 폴백."""
    if not asset_path or not Path(asset_path).exists():
        logger.warning("[compose] 에셋 파일 없음 — 단색 폴백: %s", asset_path)
        return ColorClip((_TARGET_WIDTH, _TARGET_HEIGHT), color=(40, 40, 60)).with_duration(duration)

    ext = Path(asset_path).suffix.lower()
    try:
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            return ImageClip(asset_path).with_duration(duration)
        elif ext in (".mp4", ".mov", ".avi", ".webm"):
            clip = VideoFileClip(asset_path)
            return clip
        else:
            logger.warning("[compose] 알 수 없는 에셋 형식 — 단색 폴백: %s", asset_path)
            return ColorClip((_TARGET_WIDTH, _TARGET_HEIGHT), color=(40, 40, 60)).with_duration(duration)
    except Exception as exc:
        logger.warning("[compose] 클립 로드 실패 — 단색 폴백: %s (%s)", asset_path, exc)
        return ColorClip((_TARGET_WIDTH, _TARGET_HEIGHT), color=(40, 40, 60)).with_duration(duration)


def _resize_clip(clip: object, width: int, height: int) -> object:
    """클립을 target 해상도로 resize/crop해 9:16을 강제한다."""
    try:
        import numpy as np
        current_w = clip.w  # type: ignore[attr-defined]
        current_h = clip.h  # type: ignore[attr-defined]

        if current_w == width and current_h == height:
            return clip

        # resize to cover the target (유지 비율 후 crop)
        scale_w = width / current_w
        scale_h = height / current_h
        scale = max(scale_w, scale_h)

        new_w = int(current_w * scale)
        new_h = int(current_h * scale)

        resized = clip.resized((new_w, new_h))  # type: ignore[attr-defined]

        # center crop
        x_center = new_w // 2
        y_center = new_h // 2
        x1 = x_center - width // 2
        y1 = y_center - height // 2
        cropped = resized.cropped(x1=x1, y1=y1, x2=x1 + width, y2=y1 + height)  # type: ignore[attr-defined]
        return cropped
    except Exception as exc:
        logger.warning("[compose] 클립 resize 실패 — 원본 사용: %s", exc)
        return clip


def _build_caption_clips(slots: list[dict], video_duration: float) -> list:
    """captions.slots 리스트에서 TextClip 목록을 구성한다."""
    caption_clips = []
    for slot in slots:
        text = slot.get("text", "")
        if not text:
            # text가 없는 슬롯은 건너뜀 (is_hook 슬롯 등 텍스트 미채움)
            # is_hook=True 슬롯은 hook_gen이 채워야 하지만 없으면 건너뜀
            continue

        appear_sec = float(slot.get("appear_sec", 0.0))
        if appear_sec >= video_duration:
            continue

        clip = _make_caption_clip(slot, text)
        if clip is not None:
            caption_clips.append(clip)

    return caption_clips


def _build_audio_track(
    music_mood: str,
    music_start_sec: float,
    target_lufs: float,
    video_duration: float,
    run_dir: str,
    AudioFileClip: type,
) -> object | None:
    """배경 음악 + 보이스오버를 믹싱한 오디오 트랙을 반환한다.

    음악 로드 실패 시 None 반환 (영상 합성은 계속).
    """
    try:
        from moviepy import CompositeAudioClip  # type: ignore[import]
    except ImportError:
        return None

    audio_clips = []

    # 배경 음악
    try:
        music_path = select_music(music_mood)
        if Path(music_path).exists():
            music_clip = AudioFileClip(music_path)

            # music_start_sec 오프셋 적용
            if music_start_sec > 0 and music_start_sec < music_clip.duration:
                music_clip = music_clip.subclipped(music_start_sec)

            # 음악이 영상보다 짧으면 loop
            if music_clip.duration < video_duration:
                # AudioFileClip loop
                loops_needed = int(video_duration / music_clip.duration) + 2
                from moviepy import concatenate_audioclips  # type: ignore[import]
                looped = concatenate_audioclips([music_clip] * loops_needed)
                music_clip = looped.subclipped(0, video_duration)
            else:
                music_clip = music_clip.subclipped(0, video_duration)

            # LUFS 정규화 (간략화된 볼륨 계수)
            volume_factor = 10 ** ((target_lufs - _ASSUMED_MUSIC_LUFS) / 20)
            music_clip = music_clip.with_volume_scaled(volume_factor)  # type: ignore[attr-defined]

            audio_clips.append(music_clip)
        else:
            logger.warning("[compose] 음악 파일을 찾을 수 없습니다: %s", music_path)
    except HarnessError as exc:
        logger.warning("[compose] 음악 선택 실패 — 음악 없이 계속: %s", exc)
    except Exception as exc:
        logger.warning("[compose] 음악 로드 실패 — 음악 없이 계속: %s", exc)

    # 보이스오버
    vo_path = Path(run_dir) / "voiceover.wav"
    if vo_path.exists():
        try:
            vo_clip = AudioFileClip(str(vo_path))
            if vo_clip.duration > video_duration:
                vo_clip = vo_clip.subclipped(0, video_duration)
            audio_clips.append(vo_clip)
        except Exception as exc:
            logger.warning("[compose] 보이스오버 로드 실패: %s", exc)

    if not audio_clips:
        return None

    if len(audio_clips) == 1:
        return audio_clips[0]

    try:
        return CompositeAudioClip(audio_clips)
    except Exception as exc:
        logger.warning("[compose] 오디오 믹싱 실패 — 첫 트랙만 사용: %s", exc)
        return audio_clips[0]

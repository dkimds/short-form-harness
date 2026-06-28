"""
src/analyze/audio_stats.py — 음악 타이밍·LUFS·VO 감지

ffmpeg/librosa를 사용해 음악 인 타이밍(music_start_sec), 통합 LUFS(target_lufs),
보이스오버 유무(has_voiceover)를 측정한다.
오디오 트랙이 없으면 has_voiceover=False로 계속 처리한다. (요구사항 4.1~4.4)
"""

from __future__ import annotations

import io
import json
import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AudioStats:
    """오디오 분석 결과.

    Attributes:
        music_start_sec: 음악이 시작되는 시각(초). 오디오 없으면 0.0.
        target_lufs: 통합 라우드니스(LUFS). 측정 실패 시 -23.0.
        has_voiceover: 보이스오버(VO) 트랙이 감지되면 True.
    """

    music_start_sec: float
    target_lufs: float
    has_voiceover: bool


# 오디오 부재 시 반환하는 기본값
_DEFAULT = AudioStats(music_start_sec=0.0, target_lufs=-23.0, has_voiceover=False)


def _has_audio_stream(path: str) -> bool:
    """ffprobe로 파일에 오디오 스트림이 있는지 확인한다."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False

    streams = data.get("streams", [])
    return any(s.get("codec_type") == "audio" for s in streams)


def _measure_lufs(path: str) -> float:
    """ffmpeg loudnorm 필터로 통합 LUFS를 측정한다.

    측정 실패 시 기본값 -23.0 LUFS를 반환한다.
    """
    result = subprocess.run(
        [
            "ffmpeg",
            "-i", path,
            "-af", "loudnorm=print_format=json",
            "-f", "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # loudnorm은 stderr에 JSON을 출력한다
    output = result.stderr

    # JSON 블록 추출: { ... } 패턴
    match = re.search(r"\{[^{}]+\}", output, re.DOTALL)
    if not match:
        logger.warning("loudnorm JSON을 파싱할 수 없습니다. 기본값 -23.0 LUFS 사용.")
        return -23.0

    try:
        loudnorm_data = json.loads(match.group())
        input_i = float(loudnorm_data.get("input_i", -23.0))
        # ffmpeg이 측정 불가 시 "-inf" 문자열을 반환하는 경우 처리
        return input_i if input_i == input_i else -23.0  # NaN 체크
    except (ValueError, KeyError, TypeError):
        logger.warning("loudnorm input_i 파싱 실패. 기본값 -23.0 LUFS 사용.")
        return -23.0


def _extract_wav_bytes(path: str) -> bytes:
    """ffmpeg으로 모노 22050Hz WAV 바이트를 추출한다."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-i", path,
            "-ac", "1",
            "-ar", "22050",
            "-f", "wav",
            "-",
        ],
        capture_output=True,
        timeout=120,
    )
    return result.stdout


def _detect_music_start(wav_bytes: bytes) -> float:
    """librosa onset 감지로 음악 시작 시각(초)을 찾는다.

    첫 번째 강한 onset의 시각을 반환한다. onset이 없으면 0.0.
    """
    try:
        import librosa  # type: ignore[import]

        y, sr = librosa.load(io.BytesIO(wav_bytes), sr=22050)
        onsets = librosa.onset.onset_detect(y=y, sr=sr, backtrack=True, units="time")
        if len(onsets) == 0:
            return 0.0
        return float(onsets[0])
    except Exception as exc:  # pragma: no cover
        logger.warning("librosa onset 감지 실패: %s. 0.0 사용.", exc)
        return 0.0


def _detect_voiceover(wav_bytes: bytes) -> bool:
    """librosa 스펙트럴 센트로이드 휴리스틱으로 VO 유무를 판단한다.

    mean(spectral_centroid) > 2000 Hz AND 300~3000 Hz 에너지가 유의미하면 True.
    이 감지는 의도적으로 단순화된 휴리스틱이다.
    """
    try:
        import librosa  # type: ignore[import]
        import numpy as np  # type: ignore[import]

        y, sr = librosa.load(io.BytesIO(wav_bytes), sr=22050)

        if len(y) == 0:
            return False

        # 스펙트럴 센트로이드 계산
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
        mean_centroid = float(np.mean(centroid))

        # 300~3000 Hz 대역(음성 대역) 에너지 비율 계산
        stft = np.abs(librosa.stft(y))
        freqs = librosa.fft_frequencies(sr=sr)
        speech_mask = (freqs >= 300) & (freqs <= 3000)
        speech_energy = float(np.sum(stft[speech_mask, :]))
        total_energy = float(np.sum(stft))

        if total_energy == 0:
            has_vo = False
        else:
            speech_ratio = speech_energy / total_energy
            has_vo = mean_centroid > 2000 and speech_ratio > 0.3

        logger.debug(
            "VO detection heuristic: centroid=%.1f, speech_ratio=%.3f, has_voiceover=%s",
            mean_centroid,
            speech_energy / total_energy if total_energy > 0 else 0.0,
            has_vo,
        )
        return has_vo

    except Exception as exc:  # pragma: no cover
        logger.warning("librosa VO 감지 실패: %s. False 반환.", exc)
        return False


def analyze_audio(path: str) -> AudioStats:
    """ffmpeg/librosa로 음악 인 타이밍·LUFS·VO 유무를 측정한다.

    오디오 트랙이 없으면 has_voiceover=False로 기본값을 반환한다. (요구사항 4.1~4.4)

    Args:
        path: 분석할 미디어 파일 경로.

    Returns:
        AudioStats(music_start_sec, target_lufs, has_voiceover).
    """
    try:
        # 1. 오디오 스트림 존재 여부 확인
        if not _has_audio_stream(path):
            logger.warning("No audio track found in %s", path)
            return AudioStats(music_start_sec=0.0, target_lufs=-23.0, has_voiceover=False)

        # 2. 통합 LUFS 측정
        target_lufs = _measure_lufs(path)

        # 3. WAV 바이트 추출 (onset 감지 + VO 감지에 공용)
        wav_bytes = _extract_wav_bytes(path)

        # 4. 음악 시작 타이밍 감지
        music_start_sec = _detect_music_start(wav_bytes)

        # 5. VO 감지
        has_voiceover = _detect_voiceover(wav_bytes)

        return AudioStats(
            music_start_sec=music_start_sec,
            target_lufs=target_lufs,
            has_voiceover=has_voiceover,
        )

    except Exception as exc:
        logger.warning("analyze_audio 오류 (%s): %s. 기본값 반환.", path, exc)
        return AudioStats(music_start_sec=0.0, target_lufs=-23.0, has_voiceover=False)

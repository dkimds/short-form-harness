"""
src/common/vendor_client.py — Google API 격리 래퍼

이 모듈은 프로젝트 전체에서 Google Generative AI SDK를 import하는
유일한 위치다. 다른 어떤 모듈도 ``google.genai``를 직접 import해서는
안 된다. 모든 벤더 호출은 반드시 이 파일의 ``VendorClient``를 통해서만
이루어져야 한다. (요구사항 13.5)

교체·모킹을 쉽게 하기 위해 인터페이스는 벤더 중립적인 동사
(analyze_video, generate_text, …)로 정의한다.

재시도 정책: 최대 3회, 지수 백오프 (1 s → 2 s → 4 s).
모든 재시도를 소진한 뒤에도 실패하면 ``VendorError``를 raise한다.
"""

from __future__ import annotations

import json
import logging
import struct
import time
import warnings
from typing import Callable, TypeVar

from google import genai
from google.genai import types as genai_types

from src.common.config import Config
from src.common.exceptions import VendorError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# 재시도 설정
# ---------------------------------------------------------------------------
_MAX_RETRIES = 3
_BACKOFF_SECONDS = [1, 2, 4]  # attempt 0 → 1s, attempt 1 → 2s, 그 후 raise


def _retry(operation_name: str, vendor_name: str, fn: Callable[[], T]) -> T:
    """fn()을 최대 _MAX_RETRIES 회 시도한다.

    성공하면 결과를 반환하고, 모든 시도가 실패하면 VendorError를 raise한다.

    Args:
        operation_name: 로그·에러 메시지에 표시할 작업 이름.
        vendor_name: 로그·에러 메시지에 표시할 벤더 이름.
        fn: 실행할 콜러블. 예외를 raise하면 실패로 간주한다.

    Returns:
        fn()의 반환값.

    Raises:
        VendorError: 최대 재시도 횟수를 소진한 경우.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                wait = _BACKOFF_SECONDS[attempt]
                logger.warning(
                    "[%s] %s 시도 %d/%d 실패 — %ds 후 재시도: %s",
                    vendor_name,
                    operation_name,
                    attempt + 1,
                    _MAX_RETRIES,
                    wait,
                    exc,
                )
                time.sleep(wait)
    raise VendorError(
        f"{vendor_name} API 호출이 {_MAX_RETRIES}회 재시도 후에도 실패했습니다 "
        f"(작업: {operation_name}).\n"
        f"  원인: {last_exc}\n"
        f"  해결: API 키를 확인하고 네트워크 상태를 점검하세요.\n"
        f"       할당량 초과 시 https://aistudio.google.com 에서 사용량을 확인하세요.",
        vendor=vendor_name,
        operation=operation_name,
    )


# ---------------------------------------------------------------------------
# 무음 WAV 헬퍼 (TTS 폴백용)
# ---------------------------------------------------------------------------
def _silent_wav(duration_sec: float = 1.0, sample_rate: int = 22050) -> bytes:
    """지정된 길이의 16-bit PCM 무음 WAV 바이트를 반환한다."""
    num_samples = int(sample_rate * duration_sec)
    pcm_data = b"\x00\x00" * num_samples  # 16-bit 무음
    data_size = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,            # PCM 청크 크기
        1,             # PCM 포맷
        1,             # 채널 수 (모노)
        sample_rate,
        sample_rate * 2,  # 바이트율
        2,             # 블록 정렬
        16,            # 비트 심도
        b"data",
        data_size,
    )
    return header + pcm_data


# ---------------------------------------------------------------------------
# VendorClient
# ---------------------------------------------------------------------------
class VendorClient:
    """Google Generative AI API 격리 래퍼.

    모든 Google API 호출의 단일 진입점이다. 각 퍼블릭 메서드는 재시도 및
    지수 백오프를 내장하고, 재시도를 소진하면 ``VendorError``를 raise한다.

    Args:
        config: 환경 변수에서 로드한 설정 객체.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client = genai.Client(api_key=config.google_api_key)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _parse_json_response(self, response_text: str) -> dict:
        """모델 응답에서 JSON을 추출한다.

        마크다운 코드 펜스(```json … ```)로 감싸진 경우도 처리한다.
        """
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner_lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner_lines).strip()
        return json.loads(text)

    # ------------------------------------------------------------------
    # 퍼블릭 메서드
    # ------------------------------------------------------------------

    def analyze_video(self, video_path: str, prompt: str) -> dict:
        """Gemini 비전: mp4 + 프롬프트 → 구조화 JSON.

        mp4 파일을 Files API로 업로드한 뒤 Gemini 비전 모델로 분석하고
        JSON 응답을 dict로 반환한다. (요구사항 5.1)

        Args:
            video_path: 분석할 mp4 파일의 로컬 경로.
            prompt: 분석 지침 프롬프트 텍스트.

        Returns:
            Gemini가 반환한 구조화 JSON을 파싱한 dict.

        Raises:
            VendorError: 모든 재시도 소진 후에도 실패한 경우.
        """

        def _call() -> dict:
            uploaded = self._client.files.upload(
                file=video_path,
                config=genai_types.UploadFileConfig(mime_type="video/mp4"),
            )
            # Files API: 업로드 후 ACTIVE 상태가 될 때까지 폴링
            import time as _time
            for _ in range(20):  # 최대 20초 대기
                file_info = self._client.files.get(name=uploaded.name)
                if file_info.state.name == "ACTIVE":
                    break
                _time.sleep(1)
            else:
                raise RuntimeError(f"File {uploaded.name} did not become ACTIVE in time")
            response = self._client.models.generate_content(
                model=self._config.gemini_model,
                contents=[uploaded, prompt],
            )
            return self._parse_json_response(response.text)

        return _retry("analyze_video", "Gemini", _call)

    def generate_text(
        self,
        prompt: str,
        *,
        temperature: float,
        seed: int | None,
    ) -> str:
        """Gemini 텍스트: 훅 생성 등에 사용.

        temperature와 seed를 generation_config로 전달해 호출한다.
        (요구사항 8.1, 8.2)

        Args:
            prompt: 텍스트 생성 지침 프롬프트.
            temperature: 생성 다양성 제어 (훅 생성 시 ≥ 0.8).
            seed: 재현성을 위한 시드. None이면 매 호출마다 달라진다.

        Returns:
            모델이 생성한 텍스트 문자열.

        Raises:
            VendorError: 모든 재시도 소진 후에도 실패한 경우.
        """

        def _call() -> str:
            config_kwargs: dict = {"temperature": temperature}
            if seed is not None:
                config_kwargs["seed"] = seed
            generation_config = genai_types.GenerateContentConfig(**config_kwargs)
            response = self._client.models.generate_content(
                model=self._config.gemini_model,
                contents=prompt,
                config=generation_config,
            )
            return response.text.strip()

        return _retry("generate_text", "Gemini", _call)

    def judge_video(self, video_path: str, prompt: str) -> dict:
        """Gemini 비전 자기판정: mp4 + 기준 → {verdict, reasons}.

        게이트 QA에서 최종 영상을 평가하는 데 사용한다. (요구사항 12.3)

        Args:
            video_path: 판정할 mp4 파일의 로컬 경로.
            prompt: 판정 기준이 포함된 프롬프트 텍스트.

        Returns:
            ``{"verdict": str, "reasons": list[str]}`` 형식의 dict.

        Raises:
            VendorError: 모든 재시도 소진 후에도 실패한 경우.
        """

        def _call() -> dict:
            uploaded = self._client.files.upload(
                file=video_path,
                config=genai_types.UploadFileConfig(mime_type="video/mp4"),
            )
            response = self._client.models.generate_content(
                model=self._config.gemini_model,
                contents=[uploaded, prompt],
            )
            parsed = self._parse_json_response(response.text)
            verdict = parsed.get("verdict", "")
            reasons = parsed.get("reasons", [])
            if not isinstance(reasons, list):
                reasons = [str(reasons)]
            return {"verdict": verdict, "reasons": reasons}

        return _retry("judge_video", "Gemini", _call)

    def generate_image(self, prompt: str, *, aspect_ratio: str) -> bytes:
        """Imagen: 프롬프트 → 이미지 바이트.

        Imagen 모델을 호출해 장면 이미지를 생성하고 PNG/JPEG 바이트를
        반환한다. (요구사항 10.1)

        Args:
            prompt: 이미지 생성 지침 프롬프트.
            aspect_ratio: 이미지 종횡비 문자열 (예: ``"9:16"``).

        Returns:
            생성된 이미지의 바이트 데이터.

        Raises:
            VendorError: 모든 재시도 소진 후에도 실패한 경우.
        """

        def _call() -> bytes:
            result = self._client.models.generate_images(
                model=self._config.imagen_model,
                prompt=prompt,
                config=genai_types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                ),
            )
            # 첫 번째 이미지의 바이트를 반환
            image = result.generated_images[0].image
            return image.image_bytes

        return _retry("generate_image", "Imagen", _call)

    def image_to_video(
        self,
        image: bytes,
        prompt: str,
        *,
        duration_sec: float,
    ) -> bytes:
        """Veo i2v: 이미지 → 비디오 클립 바이트. (요구사항 10.2)

        Veo predictLongRunning API를 호출해 이미지에서 비디오 클립을 생성한다.
        작업이 완료될 때까지 폴링하며, 최대 60초(20회 × 3초) 대기한다.
        모든 재시도를 소진하거나 타임아웃되면 VendorError를 raise한다.

        Args:
            image: 입력 이미지 바이트 (PNG 형식).
            prompt: 영상 생성 지침 프롬프트.
            duration_sec: 생성할 클립의 길이(초). Veo는 정수 초를 받는다.

        Returns:
            생성된 비디오 클립의 바이트 데이터 (mp4).

        Raises:
            VendorError: Veo API 호출이 모든 재시도 소진 후에도 실패한 경우,
                         또는 60초 내에 작업이 완료되지 않은 경우.
        """
        def _call() -> bytes:
            operation = self._client.models.generate_videos(
                model=self._config.veo_model,
                source=genai_types.GenerateVideosSource(
                    prompt=prompt,
                    image=genai_types.Image(image_bytes=image, mime_type="image/png"),
                ),
                config=genai_types.GenerateVideosConfig(
                    aspect_ratio="9:16",
                    number_of_videos=1,
                    # Veo는 4/6/8초 중 하나만 허용 (5·7초 등 다른 정수는 400 INVALID_ARGUMENT)
                    duration_seconds=min((4, 6, 8), key=lambda allowed: abs(allowed - duration_sec)),
                ),
            )
            # 완료될 때까지 폴링 (최대 150초 = 30회 × 5초)
            for _ in range(30):
                if operation.done:
                    break
                time.sleep(5)
                operation = self._client.operations.get(operation)
            else:
                # 루프가 break 없이 종료 → 타임아웃
                raise RuntimeError("Veo operation did not complete within 60 seconds")

            video = operation.response.generated_videos[0].video
            # Veo returns either video_bytes or a URI (download required)
            if video.video_bytes:
                return video.video_bytes
            if video.uri:
                import urllib.request
                req = urllib.request.Request(
                    video.uri,
                    headers={"x-goog-api-key": self._config.google_api_key},
                )
                with urllib.request.urlopen(req) as resp:
                    video_bytes = resp.read()
                if not video_bytes:
                    raise RuntimeError("Veo URI download returned empty bytes")
                return video_bytes
            raise RuntimeError("Veo returned empty video bytes and no URI")

        return _retry("image_to_video", "Veo", _call)

    def synthesize_speech(self, text: str, *, voice: str) -> bytes:
        """Google TTS: 텍스트 → 오디오 바이트. (요구사항 10.3)

        google-cloud-texttospeech 라이브러리가 설치된 경우 Google Cloud TTS를
        사용하고, 그렇지 않으면 무음 WAV 바이트를 반환하는 폴백으로 동작한다.

        Args:
            text: 합성할 텍스트.
            voice: 사용할 목소리 이름 (예: ``"ko-KR-Standard-A"``).

        Returns:
            합성된 오디오의 바이트 데이터 (MP3 또는 WAV).

        Raises:
            VendorError: TTS API 호출이 모든 재시도 소진 후에도 실패한 경우.
        """
        try:
            from google.cloud import texttospeech  # type: ignore[import]
        except ImportError:
            warnings.warn(
                "google-cloud-texttospeech 패키지가 설치되지 않았습니다. "
                "무음 WAV를 반환합니다. 실제 TTS를 사용하려면 "
                "`uv pip install google-cloud-texttospeech`를 실행하세요.",
                stacklevel=2,
            )
            logger.warning(
                "google-cloud-texttospeech not installed, returning silent WAV"
            )
            return _silent_wav()

        def _call() -> bytes:
            tts_client = texttospeech.TextToSpeechClient()
            synthesis_input = texttospeech.SynthesisInput(text=text)
            # voice 이름에서 언어 코드 추론 (예: "ko-KR-Standard-A" → "ko-KR")
            parts = voice.split("-")
            language_code = "-".join(parts[:2]) if len(parts) >= 2 else "ko-KR"
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
            )
            response = tts_client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            )
            return response.audio_content

        return _retry("synthesize_speech", "Google TTS", _call)

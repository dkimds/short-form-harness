"""
src/common/config.py — 환경 변수 로딩 및 검증

GOOGLE_API_KEY를 비롯한 설정 값을 환경 변수에서 읽어 불변 Config dataclass로 반환한다.
GOOGLE_API_KEY가 없으면 발급처·용도 안내를 포함한 ConfigError를 raise한다.
(요구사항 1.4, 15.1)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from src.common.exceptions import ConfigError


@dataclass(frozen=True)
class Config:
    """Google API 연동에 필요한 설정 값 모음.

    모든 필드는 생성 후 변경 불가(frozen=True). 인스턴스는 `load_config()`로만 생성한다.

    Attributes:
        google_api_key: Google AI Studio API 키. 필수.
        gemini_model: Gemini 모델 식별자. 기본값 "gemini-2.5-flash".
        imagen_model: 이미지 생성 모델 식별자. 기본값 "gemini-2.5-flash-image"
            (Nano Banana). Imagen 전용 API(generate_images)가 아니라 Gemini의
            generate_content 이미지 응답 모달리티를 사용한다 — Imagen과 별도
            quota라 Imagen 할당량 소진과 무관하게 동작한다. Imagen은 2026-08-17
            지원 종료가 예고되어 있어 신규 기본값에서 제외했다.
        veo_model: image-to-video 생성 모델 식별자. 기본값
            "gemini-omni-flash-preview" (Gemini Omni Flash). Veo 계열
            (veo-3.1-*-preview)은 이 프로젝트에서 RPM 할당량이 거의 0에
            가까워 재시도·백오프로도 429가 반복되는 것을 확인했다 — Omni
            Flash는 Veo와 완전히 분리된 quota를 쓰는 별도 모델이라 이를
            기본값으로 채택했다. 필드명은 하위 호환을 위해 유지.
        tts_voice: Google TTS 음성 식별자. 기본값 "en-US-Neural2-F".
    """

    google_api_key: str
    gemini_model: str
    imagen_model: str
    veo_model: str
    tts_voice: str


def load_config() -> Config:
    """환경 변수에서 설정을 로드해 Config 인스턴스를 반환한다.

    .env 파일이 존재하면 자동으로 로드한다(python-dotenv).
    GOOGLE_API_KEY가 설정되지 않았거나 빈 문자열이면 발급처·용도·설정 방법을
    안내하는 ConfigError를 raise한다.

    Returns:
        검증된 설정 값을 담은 불변 Config 인스턴스.

    Raises:
        ConfigError: GOOGLE_API_KEY 환경 변수가 없거나 비어 있을 때.

    Example::

        config = load_config()
        print(config.gemini_model)  # "gemini-2.5-flash"
    """
    # .env 파일이 있으면 환경 변수로 로드 (이미 설정된 값은 덮어쓰지 않음)
    load_dotenv()

    google_api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not google_api_key:
        raise ConfigError(
            "GOOGLE_API_KEY 환경 변수가 설정되지 않았습니다.\n"
            "\n"
            "  필요한 환경 변수 : GOOGLE_API_KEY\n"
            "  발급처           : https://aistudio.google.com\n"
            "  용도             : Gemini(레퍼런스 분석·훅 생성·QA 판정),\n"
            "                     Imagen(장면 이미지 생성),\n"
            "                     Veo(히어로 클립 image-to-video),\n"
            "                     Google TTS(보이스오버, 옵션)\n"
            "\n"
            "  설정 방법        : 프로젝트 루트에서 다음 명령을 실행하세요.\n"
            "                       cp .env.example .env\n"
            "                     이후 .env 파일을 열고 GOOGLE_API_KEY=<your-key> 를 채워 넣으세요.",
            var_name="GOOGLE_API_KEY",
        )

    return Config(
        google_api_key=google_api_key,
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        imagen_model=os.environ.get("IMAGEN_MODEL", "gemini-2.5-flash-image"),
        veo_model=os.environ.get("VEO_MODEL", "gemini-omni-flash-preview"),
        tts_voice=os.environ.get("TTS_VOICE", "en-US-Neural2-F"),
    )

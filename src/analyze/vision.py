"""
src/analyze/vision.py — Gemini 비전 분석

prompts/analyze_vision.md 프롬프트를 로드해 VendorClient.analyze_video를 호출하고,
응답에서 narrative.beats / captions.slots / visual / audio.music_mood·vo_style 섹션을
추출·반환한다. (요구사항 5.1~5.6)

설계 의도:
  - 프롬프트 파일은 호출 시점에 로드한다(임포트 시가 아님). 파일이 없으면 None 반환.
  - VendorClient 내부에서 최대 3회 재시도(지수 백오프)를 처리한다.
  - VendorError가 발생하면(모든 재시도 소진) 오류를 기록하고 None 반환.
  - 필수 섹션 중 일부가 누락된 경우에도 파싱된 결과를 반환한다(부분 결과가 None보다 낫다).
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.common.exceptions import VendorError
from src.common.vendor_client import VendorClient

logger = logging.getLogger(__name__)

# 필수 최상위 섹션 — 모두 존재해야 이상적이지만 부분 결과도 허용한다
_REQUIRED_SECTIONS = ("narrative", "captions", "visual", "audio")

# prompts/analyze_vision.md 경로 (프로젝트 루트 기준)
_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "analyze_vision.md"


def analyze_vision(client: VendorClient, path: str) -> dict | None:
    """prompts/analyze_vision.md 프롬프트로 Gemini 비전 호출.

    narrative.beats / captions.slots / visual / audio.music_mood·vo_style
    추출 (요구사항 5.1~5.5). 실패 시 최대 3회 재시도 후 None 반환 → 건너뜀 (5.6)

    Args:
        client: Google API 격리 래퍼. analyze_video 메서드로 Gemini를 호출한다.
        path: 분석할 레퍼런스 mp4 파일의 로컬 경로.

    Returns:
        narrative, captions, visual, audio 섹션을 포함하는 dict.
        필수 섹션이 일부 누락되어도 파싱된 결과를 반환한다.
        프롬프트 파일 부재 또는 VendorError 발생 시 None.
    """
    # 1. 프롬프트 파일 로드 (호출 시점에 읽음)
    prompt = _load_prompt()
    if prompt is None:
        return None

    # 2. Gemini 비전 호출 — 재시도는 VendorClient 내부에서 처리
    try:
        raw: dict = client.analyze_video(path, prompt)
    except VendorError as exc:
        logger.error(
            "비전 분석 실패(모든 재시도 소진): %s — 해당 레퍼런스를 건너뜁니다. (경로: %s)",
            exc,
            path,
        )
        return None

    # 3. 응답 검증 및 섹션 추출
    return _extract_sections(raw, path)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _load_prompt() -> str | None:
    """prompts/analyze_vision.md 파일을 읽어 문자열로 반환한다.

    파일이 없으면 오류를 기록하고 None을 반환한다.
    """
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error(
            "비전 분석 프롬프트 파일을 찾을 수 없습니다: %s\n"
            "  해결: 레포 루트의 prompts/analyze_vision.md 파일이 존재하는지 확인하세요.",
            _PROMPT_PATH,
        )
        return None


def _extract_sections(raw: dict, path: str) -> dict:
    """응답 dict에서 필수 섹션을 확인하고, 누락 시 경고를 기록한다.

    부분 결과라도 반환한다 — None보다 부분 결과가 다운스트림 합성에 유용하다.

    Args:
        raw: VendorClient.analyze_video가 반환한 파싱된 dict.
        path: 로그 컨텍스트용 레퍼런스 경로.

    Returns:
        추출된 dict. 일부 섹션이 빠진 경우에도 raw를 그대로 반환한다.
    """
    missing = [sec for sec in _REQUIRED_SECTIONS if sec not in raw]
    if missing:
        logger.warning(
            "비전 응답에서 다음 필수 섹션이 누락되었습니다: %s (경로: %s)\n"
            "  다운스트림 합성에서 이 값들은 기본값으로 대체됩니다.",
            missing,
            path,
        )

    # audio 섹션 내 music_mood / vo_style 세부 필드 확인 (요구사항 5.5)
    if "audio" in raw and isinstance(raw["audio"], dict):
        audio_section: dict = raw["audio"]
        missing_audio_fields = [
            field for field in ("music_mood", "vo_style")
            if field not in audio_section
        ]
        if missing_audio_fields:
            logger.warning(
                "audio 섹션에서 다음 필드가 누락되었습니다: %s (경로: %s)",
                missing_audio_fields,
                path,
            )

    return raw

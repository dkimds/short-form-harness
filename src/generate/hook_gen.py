"""
src/generate/hook_gen.py — 비결정적 훅 생성 (요구사항 8.1~8.6)

hook_gen 모듈은 `prompts/hook_gen.md` 프롬프트를 사용해 VendorClient를 통해
Gemini에 훅 텍스트 생성을 요청한다.

핵심 설계 원칙:
- temperature ≥ 0.8, seed는 매 호출마다 random.randint로 생성 → 비결정성 보장
- VendorClient 내부 재시도(3회)가 소진되어 VendorError가 발생하면 여기서 3회 더 재시도
- 모든 재시도 소진 시 폴백 훅 텍스트 사용 및 경고 기록
- src/analyze/ 를 절대 import하지 않는다 (요구사항 13.2)
"""

from __future__ import annotations

import copy
import logging
import random
from pathlib import Path

from src.common.exceptions import VendorError
from src.common.vendor_client import VendorClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_HOOK_GEN_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "hook_gen.md"
_TEMPERATURE = 0.9          # ≥ 0.8 (요구사항 8.2)
_MAX_RETRIES = 3             # VendorError 발생 시 최대 재시도 횟수 (요구사항 8.6)
_SEED_MAX = 2**31 - 1       # random.randint 상한 (요구사항 8.2)

# 폴백 훅 텍스트 — 모든 재시도 소진 시 사용 (요구사항 8.6)
_FALLBACK_HOOK = "이거 진짜 달라졌어 ✨"


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _load_prompt_template() -> str:
    """prompts/hook_gen.md 파일을 읽어 템플릿 문자열로 반환한다."""
    if not _HOOK_GEN_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"훅 생성 프롬프트 파일이 없습니다: {_HOOK_GEN_PROMPT_PATH}\n"
            "  해결: prompts/hook_gen.md 파일을 생성하세요."
        )
    return _HOOK_GEN_PROMPT_PATH.read_text(encoding="utf-8")


def _build_prompt(template: str, brief: dict, profile: dict) -> str:
    """프롬프트 템플릿에 brief와 profile의 컨텍스트를 채운다.

    Args:
        template: hook_gen.md의 원본 텍스트.
        brief: build_brief()가 반환한 브리프 dict.
        profile: style_profile dict.

    Returns:
        완성된 프롬프트 문자열.
    """
    # brief에서 제품/주제 추출
    user_input = brief.get("user_input", {})
    product_subject = user_input.get("value", "스킨케어 제품")
    if user_input.get("kind") in ("image", "video"):
        # 파일 입력인 경우 파일명만 사용
        product_subject = Path(product_subject).stem if product_subject else "스킨케어 제품"

    # profile에서 비주얼·오디오·훅 컨텍스트 추출
    audio = profile.get("audio", {})
    visual = profile.get("visual", {})
    music_mood = audio.get("music_mood", "soft_upbeat_aesthetic")
    color_grade = visual.get("color_grade", "warm_soft_pastel")
    lighting = visual.get("lighting", "natural_window_soft")

    # 훅 비트의 intent 추출 (narrative.beats 중 role=hook 인 첫 번째)
    hook_intent = "영상 첫 3초에 시청자의 스크롤을 멈추게 한다"
    for beat in profile.get("narrative", {}).get("beats", []):
        if beat.get("role") == "hook":
            hook_intent = beat.get("intent", hook_intent)
            break

    # 템플릿 변수 치환
    filled = template.replace("{product_subject}", product_subject)
    filled = filled.replace("{music_mood}", music_mood)
    filled = filled.replace("{color_grade}", color_grade)
    filled = filled.replace("{lighting}", lighting)
    filled = filled.replace("{hook_intent}", hook_intent)
    return filled


# ---------------------------------------------------------------------------
# 퍼블릭 API
# ---------------------------------------------------------------------------

def generate_hook(
    client: VendorClient,
    brief: dict,
    profile: dict,
    *,
    seed: int | None = None,
) -> str:
    """훅 텍스트를 비결정적으로 생성한다. (요구사항 8.1, 8.2, 8.3, 8.6)

    `prompts/hook_gen.md` 프롬프트를 로드하고 VendorClient.generate_text를
    호출해 훅 텍스트를 생성한다. temperature ≥ 0.8, seed는 미지정 시
    매 호출마다 random.randint로 생성해 비결정성을 보장한다.

    VendorError 발생 시 최대 3회 재시도하고, 그래도 실패하면 폴백 훅
    텍스트(_FALLBACK_HOOK)를 사용하고 경고를 기록한다.

    Args:
        client: VendorClient 인스턴스.
        brief: build_brief()가 반환한 브리프 dict.
        profile: style_profile dict (visual, audio, narrative 섹션 참조).
        seed: 명시적 seed 값. None이면 매 호출마다 random.randint로 생성.

    Returns:
        생성된 훅 텍스트 (단일 줄 문자열).
    """
    template = _load_prompt_template()
    prompt = _build_prompt(template, brief, profile)

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        # seed가 지정되지 않으면 매 시도마다 새 seed 생성 (요구사항 8.2)
        effective_seed = seed if seed is not None else random.randint(0, _SEED_MAX)
        try:
            hook_text = client.generate_text(
                prompt,
                temperature=_TEMPERATURE,
                seed=effective_seed,
            )
            # 빈 텍스트나 공백만 있는 경우 재시도
            stripped = hook_text.strip()
            if stripped:
                return stripped
            raise ValueError("빈 훅 텍스트가 반환됐습니다.")
        except (VendorError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "[hook_gen] 훅 생성 시도 %d/%d 실패: %s",
                attempt + 1,
                _MAX_RETRIES,
                exc,
            )

    # 모든 재시도 소진 — 폴백 사용 (요구사항 8.6)
    logger.warning(
        "[hook_gen] 훅 생성이 %d회 재시도 후에도 실패했습니다. "
        "폴백 훅 텍스트를 사용합니다: %r (마지막 오류: %s)",
        _MAX_RETRIES,
        _FALLBACK_HOOK,
        last_error,
    )
    return _FALLBACK_HOOK


def fill_hook_slot(profile: dict, hook_text: str) -> dict:
    """captions.slots 중 is_hook=true 슬롯에만 훅 텍스트를 채운다. (요구사항 8.4, 8.5)

    원본 profile을 변경하지 않고 깊은 복사본을 반환한다. is_hook=false인
    나머지 슬롯은 변경하지 않는다.

    Args:
        profile: style_profile dict. captions.slots 배열을 포함해야 한다.
        hook_text: generate_hook()이 반환한 훅 텍스트.

    Returns:
        is_hook=true 슬롯의 text 필드가 hook_text로 채워진 새 profile dict.
        (원본은 변경되지 않음)
    """
    filled_profile = copy.deepcopy(profile)

    captions = filled_profile.get("captions", {})
    slots = captions.get("slots", [])

    for slot in slots:
        if slot.get("is_hook") is True:
            slot["text"] = hook_text

    return filled_profile

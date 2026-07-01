"""
src/generate/brief.py — 브리프 생성 (요구사항 7.1~7.4, 16.3)

UserInput과 style_profile을 결합해 생성 파이프라인의 입력 브리프를 구성한다.

핵심 설계 원칙:
- UserInput: kind("text"|"image"|"video"), value(텍스트 또는 파일 경로)
- build_brief: profile의 narrative/captions/visual/pacing 섹션 + UserInput 결합
  - image/video 입력이면 파일 존재 여부 및 형식(jpg/png/mp4/mov) 검증
  - 유효하지 않으면 InputError raise
- write_prompt_txt: hook_gen 완료 후 호출 — prompt.txt에 사람이 읽을 수 있는 형태로 기록
- src/analyze/ 를 절대 import하지 않는다 (요구사항 13.2)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from src.common.exceptions import InputError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 지원 파일 형식 상수
# ---------------------------------------------------------------------------

_SUPPORTED_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png"})
_SUPPORTED_VIDEO_EXTS = frozenset({".mp4", ".mov"})
_ALL_SUPPORTED_EXTS = _SUPPORTED_IMAGE_EXTS | _SUPPORTED_VIDEO_EXTS

# 크리에이터(인물) 참조 사진은 이미지 형식만 허용 (동영상 미지원)
_SUPPORTED_CREATOR_PHOTO_EXTS = _SUPPORTED_IMAGE_EXTS


# ---------------------------------------------------------------------------
# 데이터클래스
# ---------------------------------------------------------------------------

@dataclass
class UserInput:
    """사용자 입력을 표현하는 데이터 클래스.

    Attributes:
        kind: 입력 종류 — "text" (텍스트 문자열), "image" (이미지 파일 경로),
              "video" (영상 파일 경로)
        value: 텍스트 내용 또는 파일 경로 문자열
    """
    kind: Literal["text", "image", "video"]
    value: str


# ---------------------------------------------------------------------------
# 퍼블릭 API
# ---------------------------------------------------------------------------

def build_brief(
    profile: dict,
    user_input: UserInput,
    *,
    profile_path: str = "",
    creator_photo_path: str | None = None,
) -> dict:
    """style_profile과 UserInput을 결합해 브리프 dict를 생성한다.

    profile에서 narrative·captions·visual·pacing 섹션을 추출하고,
    user_input의 제품/주제 컨텍스트와 결합한다.

    image/video 입력인 경우:
      - 파일 존재 여부 검증
      - 확장자가 지원 형식(jpg/jpeg/png/mp4/mov)인지 검증
      - 검증 실패 시 InputError raise

    creator_photo_path가 주어지면(선택, "권장 - 크리에이터" 항목) 별도로
    파일 존재 여부·형식(jpg/jpeg/png)을 검증한다. user_input과는 독립적인
    입력이며, --input이 제품/주제를 나타내는 의미를 바꾸지 않는다.

    반환된 brief의 run_dir 필드는 caller가 설정한다.

    Args:
        profile: analyze 단계에서 생성한 style_profile dict.
                 narrative, captions, visual, pacing 섹션을 포함해야 한다.
        user_input: UserInput 인스턴스 (kind + value).
        profile_path: 입력 프로파일 JSON 파일의 경로 (선택, 기본값: "").
                      write_prompt_txt에서 기록에 사용된다.
        creator_photo_path: 크리에이터(인물) 참조 사진 경로 (선택, 기본값: None).
                      주어지면 hook·application 장면 생성 시 인물 일관성
                      유지를 위한 참조 이미지로 사용된다.

    Returns:
        브리프 dict — 키: user_input, narrative, captions, visual, pacing,
                         run_dir, profile_path, creator_photo_path

    Raises:
        InputError: image/video 입력이 유효하지 않을 때
                    (파일 없음 또는 지원하지 않는 형식), 또는
                    creator_photo_path가 유효하지 않을 때.
    """
    # image/video 입력 검증
    if user_input.kind in ("image", "video"):
        _validate_file_input(user_input)

    # 크리에이터 사진 검증 (선택적 입력)
    if creator_photo_path:
        _validate_creator_photo(creator_photo_path)

    brief: dict = {
        "user_input": {
            "kind": user_input.kind,
            "value": user_input.value,
        },
        "narrative": profile.get("narrative", {}),
        "captions": profile.get("captions", {}),
        "visual": profile.get("visual", {}),
        "pacing": profile.get("pacing", {}),
        # caller가 설정하는 필드
        "run_dir": "",
        "profile_path": profile_path,
        "creator_photo_path": creator_photo_path or "",
    }

    logger.info(
        "[brief] 브리프 생성 완료: kind=%s, value=%.40s, creator_photo=%s",
        user_input.kind,
        user_input.value,
        bool(creator_photo_path),
    )
    return brief


def write_prompt_txt(brief: dict, hook_text: str, run_dir: str) -> None:
    """사람이 읽을 수 있는 prompt.txt를 run_dir에 저장한다.

    hook_gen이 완료된 후에 호출된다. hook_text가 이 시점에 확정되어
    prompt.txt에 기록된다.

    기록 내용:
    - 생성 타임스탬프
    - user_input (kind + value)
    - hook_text (생성된 훅)
    - profile_path (입력 프로파일 경로)
    - run_dir

    Args:
        brief: build_brief()가 반환한 브리프 dict.
        hook_text: generate_hook()이 반환한 훅 텍스트.
        run_dir: outputs/<run_id>/ 절대 경로.

    Returns:
        None (요구사항 7.4, 16.3)
    """
    dest = Path(run_dir) / "prompt.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)

    user_input = brief.get("user_input", {})
    profile_path = brief.get("profile_path", "")
    creator_photo_path = brief.get("creator_photo_path", "")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "=" * 60,
        "숏폼 생성 파이프라인 — 입력 프롬프트 기록",
        "=" * 60,
        f"타임스탬프    : {timestamp}",
        f"run_dir      : {run_dir}",
        f"profile_path : {profile_path}",
        "-" * 60,
        "사용자 입력",
        f"  kind  : {user_input.get('kind', '')}",
        f"  value : {user_input.get('value', '')}",
        f"  creator_photo : {creator_photo_path or '(없음)'}",
        "-" * 60,
        "생성된 훅 텍스트",
        f"  {hook_text}",
        "=" * 60,
    ]

    content = "\n".join(lines) + "\n"
    dest.write_text(content, encoding="utf-8")
    logger.info("[brief] prompt.txt 저장 완료: %s", dest)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _validate_file_input(user_input: UserInput) -> None:
    """image/video 입력의 파일 존재 여부 및 형식을 검증한다.

    Args:
        user_input: kind가 "image" 또는 "video"인 UserInput 인스턴스.

    Raises:
        InputError: 파일이 존재하지 않거나 지원하지 않는 형식일 때.
    """
    path = Path(user_input.value)
    ext = path.suffix.lower()

    # 형식 검증 (파일 존재 여부보다 먼저 확인하여 더 명확한 에러 제공)
    if user_input.kind == "image":
        if ext not in _SUPPORTED_IMAGE_EXTS:
            raise InputError(
                f"지원하지 않는 이미지 형식입니다: '{path.name}' (확장자: {ext!r})\n"
                f"  지원 형식: {', '.join(sorted(_SUPPORTED_IMAGE_EXTS))} (jpg/jpeg/png)\n"
                f"  해결: 지원되는 형식의 이미지 파일로 다시 시도하세요.",
                path=str(path),
                kind="image",
            )
    elif user_input.kind == "video":
        if ext not in _SUPPORTED_VIDEO_EXTS:
            raise InputError(
                f"지원하지 않는 영상 형식입니다: '{path.name}' (확장자: {ext!r})\n"
                f"  지원 형식: {', '.join(sorted(_SUPPORTED_VIDEO_EXTS))} (mp4/mov)\n"
                f"  해결: 지원되는 형식의 영상 파일로 다시 시도하세요.",
                path=str(path),
                kind="video",
            )

    # 파일 존재 여부 검증
    if not path.exists():
        raise InputError(
            f"파일을 찾을 수 없습니다: {path.resolve()}\n"
            f"  kind: {user_input.kind}\n"
            f"  해결: 경로가 올바른지, 파일이 존재하는지 확인하세요.",
            path=str(path),
            kind=user_input.kind,
        )


def _validate_creator_photo(creator_photo_path: str) -> None:
    """크리에이터(인물) 참조 사진의 존재 여부 및 형식을 검증한다.

    Args:
        creator_photo_path: 검증할 이미지 파일 경로.

    Raises:
        InputError: 파일이 존재하지 않거나 지원하지 않는 형식일 때.
    """
    path = Path(creator_photo_path)
    ext = path.suffix.lower()

    if ext not in _SUPPORTED_CREATOR_PHOTO_EXTS:
        raise InputError(
            f"지원하지 않는 크리에이터 사진 형식입니다: '{path.name}' (확장자: {ext!r})\n"
            f"  지원 형식: {', '.join(sorted(_SUPPORTED_CREATOR_PHOTO_EXTS))} (jpg/jpeg/png)\n"
            f"  해결: 지원되는 형식의 이미지 파일로 다시 시도하세요.",
            path=str(path),
            kind="creator_photo",
        )

    if not path.exists():
        raise InputError(
            f"크리에이터 사진 파일을 찾을 수 없습니다: {path.resolve()}\n"
            f"  해결: --creator-photo 경로가 올바른지 확인하세요.",
            path=str(path),
            kind="creator_photo",
        )

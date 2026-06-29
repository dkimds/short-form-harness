"""
src/generate/assets.py — P0 에셋 생성 (Imagen 이미지 + TTS)

요구사항: 10.1, 10.3, 10.4, 10.5, 10.6, 10.7

핵심 설계 원칙:
- 모든 imagen_image 타입 숏에 대해 VendorClient.generate_image 호출
- 생성된 이미지의 9:16 비율(576×1024) 검증 (허용오차 ±0.05)
- 개별 실패 시: 폴백 이미지(단색 배경 + 텍스트) 생성 — 전체 run을 중단하지 않음
- has_voiceover=True이면 VendorClient.synthesize_speech 호출
- 레퍼런스 mp4를 소스로 절대 사용하지 않음 (요구사항 10.7)
- src/analyze/ import 금지 (요구사항 13.2)
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.common.exceptions import VendorError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 폴백 이미지 크기 (9:16 = 576×1024)
_FALLBACK_WIDTH = 576
_FALLBACK_HEIGHT = 1024

# 기본 폴백 배경색 (따뜻한 파스텔)
_FALLBACK_BG_COLOR = "#F5E6EF"

# 9:16 비율 검증 허용오차
_RATIO_TOLERANCE = 0.05

# 목표 비율
_TARGET_RATIO = 9 / 16  # ≈ 0.5625

# 기본 보이스오버 목소리
_DEFAULT_VOICE = "ko-KR-Standard-A"

# 폴백 프롬프트 최대 길이
_MAX_PROMPT_CHARS = 60


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """#RRGGBB 형식 hex 색상을 RGB 튜플로 변환한다.

    파싱에 실패하면 기본 파스텔 색상 (245, 230, 239)을 반환한다.
    """
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return (245, 230, 239)
    try:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return (r, g, b)
    except ValueError:
        return (245, 230, 239)


def _lighten_color(rgb: tuple[int, int, int], factor: float = 0.3) -> tuple[int, int, int]:
    """RGB 색상을 factor 비율로 밝게(white 방향으로) 조정한다."""
    r, g, b = rgb
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return (r, g, b)


def _get_fallback_bg_color(profile: dict) -> tuple[int, int, int]:
    """profile.visual.accent_color를 밝게 한 배경색, 없으면 기본 파스텔 반환."""
    accent = profile.get("visual", {}).get("accent_color", "")
    if accent:
        rgb = _hex_to_rgb(accent)
        return _lighten_color(rgb, factor=0.35)
    return _hex_to_rgb(_FALLBACK_BG_COLOR)


def _generate_fallback_image(shot: dict, profile: dict, out_path: Path) -> None:
    """PIL을 사용해 폴백 이미지(단색 배경 + 텍스트)를 생성하고 저장한다.

    PIL이 없는 환경에서는 numpy 기반 최소 PNG를 생성한다.
    폴백 이미지는 항상 576×1024 (9:16)이다.

    Args:
        shot: 숏 dict (role, prompt 포함)
        profile: style_profile dict
        out_path: 저장 경로
    """
    role = shot.get("role", "shot")
    prompt = shot.get("prompt", "")
    # 이모지 제거 (PIL 기본 폰트가 이모지를 렌더링 못함)
    prompt_clean = _strip_emoji(prompt)
    prompt_truncated = prompt_clean[:_MAX_PROMPT_CHARS]

    bg_color = _get_fallback_bg_color(profile)

    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore[import]

        img = Image.new("RGB", (_FALLBACK_WIDTH, _FALLBACK_HEIGHT), color=bg_color)
        draw = ImageDraw.Draw(img)

        # 폰트: 기본 PIL 폰트 사용 (별도 폰트 파일 불필요)
        font = ImageFont.load_default()

        # 텍스트 준비: role + 프롬프트
        lines = [f"[{role}]", prompt_truncated]

        y = _FALLBACK_HEIGHT // 2 - 30
        for line in lines:
            if not line:
                continue
            # 텍스트 너비 측정 (PIL >= 10.0 방식)
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]
            except AttributeError:
                # 구버전 PIL 폴백
                text_w, text_h = draw.textsize(line, font=font)  # type: ignore[attr-defined]

            x = (_FALLBACK_WIDTH - text_w) // 2

            # 그림자 (1px 오프셋, 검정)
            draw.text((x + 1, y + 1), line, fill=(0, 0, 0), font=font)
            # 본문 (흰색)
            draw.text((x, y), line, fill=(255, 255, 255), font=font)
            y += text_h + 8

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path), format="PNG")
        logger.debug("[assets] 폴백 이미지 저장(PIL): %s", out_path)

    except ImportError:
        # PIL 없으면 numpy 기반 단색 PNG 생성
        logger.warning("[assets] PIL 없음 — numpy 폴백 이미지 생성: %s", out_path)
        _generate_fallback_image_numpy(bg_color, out_path)


def _generate_fallback_image_numpy(
    bg_color: tuple[int, int, int], out_path: Path
) -> None:
    """numpy로 최소 PNG 바이너리를 작성한다. (PIL 완전 부재 시 최후 수단)"""
    import struct
    import zlib

    w, h = _FALLBACK_WIDTH, _FALLBACK_HEIGHT
    r, g, b = bg_color

    # 각 행: 필터 바이트(0x00) + RGB 픽셀들
    row = bytes([0x00]) + bytes([r, g, b] * w)
    raw = row * h
    compressed = zlib.compress(raw)

    def chunk(name: bytes, data: bytes) -> bytes:
        c = name + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png_data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png_data)
    logger.debug("[assets] 폴백 이미지 저장(numpy): %s", out_path)


def _strip_emoji(text: str) -> str:
    """이모지 및 비-ASCII 특수문자를 제거해 PIL 기본 폰트에서 안전한 문자열을 반환한다.

    한글은 PIL 기본 폰트에서 지원되지 않지만, 이 함수는 이모지(U+1F000 이상)와
    기타 프라이빗/심볼 범위만 제거한다. 한글 제거는 하지 않는다.
    """
    result = []
    for ch in text:
        cp = ord(ch)
        # 이모지 범위 제외: U+1F000 이상 (이모지 대부분), 및 일부 기호
        if cp >= 0x1F000:
            continue
        # 기타 심볼 범위 제외 (Miscellaneous Symbols, Dingbats 등)
        if 0x2600 <= cp <= 0x27BF:
            continue
        result.append(ch)
    return "".join(result)


def _verify_ratio(image_bytes: bytes) -> bool:
    """이미지 바이트에서 너비:높이 비율이 9:16에 가까운지 검증한다.

    PIL로 이미지를 열어 비율을 확인한다. PIL이 없으면 True를 반환해
    비율 오류 시 폴백 생성을 막지 않도록 한다.

    Args:
        image_bytes: PNG/JPEG 이미지 바이트

    Returns:
        True if ratio ≈ 9/16 (허용오차 ±0.05), otherwise False.
    """
    try:
        from PIL import Image  # type: ignore[import]
        import io

        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if h == 0:
            return False
        ratio = w / h
        return abs(ratio - _TARGET_RATIO) <= _RATIO_TOLERANCE
    except ImportError:
        # PIL 없으면 검증 불가 → 통과로 처리
        logger.warning("[assets] PIL 없음 — 비율 검증을 건너뜁니다.")
        return True
    except Exception as exc:
        logger.warning("[assets] 비율 검증 중 오류 — 통과로 처리: %s", exc)
        return True


def _build_vo_text(profile: dict) -> str:
    """profile의 captions.slots에서 보이스오버 텍스트를 구성한다.

    슬롯의 텍스트가 없으면 generic 한국어 문장을 반환한다.
    """
    slots = profile.get("captions", {}).get("slots", [])
    texts = []
    for slot in slots:
        text = slot.get("text", "")
        if text and text.strip():
            texts.append(text.strip())

    if texts:
        return " ".join(texts)
    # generic 폴백 문구
    return "글로우 세럼으로 피부가 달라졌어요"


# ---------------------------------------------------------------------------
# 퍼블릭 API
# ---------------------------------------------------------------------------

def render_assets(
    client: object,
    shotlist: dict,
    profile: dict,
    run_dir: str,
    *,
    voice: str = _DEFAULT_VOICE,
) -> dict:
    """P0 에셋 생성: 모든 imagen_image 숏에 대해 이미지를 생성하고,
    has_voiceover=True이면 TTS 보이스오버를 생성한다.

    동작 순서:
    1. shotlist["shots"]를 순회해 asset_type="imagen_image"인 숏마다
       client.generate_image(prompt, aspect_ratio="9:16")를 호출한다.
    2. 반환된 이미지 바이트를 run_dir/shot_{index:02d}.png에 저장한다.
    3. 9:16 비율 검증; 비율 불일치 또는 VendorError이면 폴백 이미지를 생성한다.
    4. shotlist의 해당 shot["asset_path"]에 저장 경로를 기록한다.
    5. profile.audio.has_voiceover == True이면 client.synthesize_speech를 호출해
       run_dir/voiceover.wav에 저장한다.

    Args:
        client: VendorClient 인스턴스 (generate_image, synthesize_speech 메서드 필요)
        shotlist: build_shotlist()가 반환한 숏리스트 dict (in-place 수정됨)
        profile: style_profile dict
        run_dir: outputs/<run_id>/ 디렉터리 절대 경로
        voice: TTS 목소리 이름 (기본값: "ko-KR-Standard-A")

    Returns:
        asset_path가 채워진 shotlist dict (in-place 수정 후 반환)

    Note:
        - 개별 숏 실패는 경고를 남기고 폴백 이미지를 사용해 run을 계속한다.
        - 레퍼런스 mp4는 절대 소스로 사용하지 않는다. (요구사항 10.7)
        - src/analyze/ 는 import하지 않는다. (요구사항 13.2)
    """
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    shots: list[dict] = shotlist.get("shots", [])

    for shot in shots:
        asset_type = shot.get("asset_type", "")
        index: int = shot.get("index", 0)
        out_path = run_path / f"shot_{index:02d}.png"

        if asset_type == "imagen_image":
            prompt = shot.get("prompt", "")
            _render_image_shot(
                client=client,
                shot=shot,
                prompt=prompt,
                profile=profile,
                out_path=out_path,
            )
        else:
            # P0에서는 imagen_image 외 타입 (veo_i2v 등)도 이미지로 처리
            # (veo_i2v는 P1에서 실제 구현)
            if asset_type:
                logger.debug(
                    "[assets] shot %d: asset_type=%s — P0에서는 건너뜀",
                    index,
                    asset_type,
                )
            continue

        shot["asset_path"] = str(out_path)
        logger.debug("[assets] shot %d 저장: %s", index, out_path)

    # 보이스오버 생성
    has_voiceover = profile.get("audio", {}).get("has_voiceover", False)
    if has_voiceover:
        _render_voiceover(
            client=client,
            profile=profile,
            run_path=run_path,
            voice=voice,
        )

    logger.info(
        "[assets] 에셋 생성 완료: %d shots 처리, voiceover=%s",
        len(shots),
        has_voiceover,
    )
    return shotlist


def _render_image_shot(
    client: object,
    shot: dict,
    prompt: str,
    profile: dict,
    out_path: Path,
) -> None:
    """단일 imagen_image 숏에 대해 이미지를 생성하고 out_path에 저장한다.

    VendorError가 발생하거나 9:16 비율이 맞지 않으면 폴백 이미지를 생성한다.
    어떤 경우에도 예외를 raise하지 않는다 — 실패는 경고로만 기록된다.

    Args:
        client: VendorClient 인스턴스
        shot: 해당 숏 dict (role, prompt 등 포함)
        prompt: Imagen에 전달할 프롬프트
        profile: style_profile dict (폴백 색상에 사용)
        out_path: 저장 대상 경로
    """
    index = shot.get("index", "?")
    use_fallback = False
    image_bytes: bytes | None = None

    # 1) 이미지 생성 시도
    try:
        image_bytes = client.generate_image(prompt, aspect_ratio="9:16")  # type: ignore[union-attr]
    except VendorError as exc:
        logger.warning(
            "[assets] shot %s: Imagen 호출 실패 — 폴백 이미지 사용. 원인: %s",
            index,
            exc,
        )
        use_fallback = True
    except Exception as exc:
        logger.warning(
            "[assets] shot %s: 예상치 못한 오류 — 폴백 이미지 사용. 원인: %s",
            index,
            exc,
        )
        use_fallback = True

    # 2) 비율 검증
    if not use_fallback and image_bytes is not None:
        if not _verify_ratio(image_bytes):
            logger.warning(
                "[assets] shot %s: 생성된 이미지가 9:16 비율이 아님 — 폴백 이미지 사용.",
                index,
            )
            use_fallback = True

    # 3) 저장 또는 폴백
    if use_fallback or image_bytes is None:
        _generate_fallback_image(shot, profile, out_path)
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(image_bytes)
        logger.debug("[assets] shot %s: 이미지 저장 완료 (%d bytes)", index, len(image_bytes))


def _render_voiceover(
    client: object,
    profile: dict,
    run_path: Path,
    voice: str,
) -> None:
    """보이스오버 오디오를 생성하고 run_path/voiceover.wav에 저장한다.

    실패 시 경고만 기록하고 계속 진행한다 (run을 중단하지 않음).

    Args:
        client: VendorClient 인스턴스
        profile: style_profile dict
        run_path: run 디렉터리 Path
        voice: TTS 목소리 이름
    """
    vo_text = _build_vo_text(profile)
    vo_path = run_path / "voiceover.wav"

    try:
        audio_bytes = client.synthesize_speech(vo_text, voice=voice)  # type: ignore[union-attr]
        vo_path.write_bytes(audio_bytes)
        logger.info("[assets] 보이스오버 저장: %s (%d bytes)", vo_path, len(audio_bytes))
    except VendorError as exc:
        logger.warning(
            "[assets] 보이스오버 생성 실패 — VO 없이 계속 진행. 원인: %s", exc
        )
    except Exception as exc:
        logger.warning(
            "[assets] 보이스오버 생성 중 예상치 못한 오류 — VO 없이 계속 진행. 원인: %s", exc
        )

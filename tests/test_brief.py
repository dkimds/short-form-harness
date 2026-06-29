"""
tests/test_brief.py — build_brief 및 write_prompt_txt 단위 테스트

요구사항: 7.1~7.4, 16.3
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.generate.brief import UserInput, build_brief, write_prompt_txt
from src.common.exceptions import InputError


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_profile() -> dict:
    """최소한의 유효한 style_profile dict."""
    return {
        "narrative": {
            "beats": [
                {"role": "hook", "start_sec": 0.0, "end_sec": 1.0, "shot_type": "closeup", "intent": "grab attention"}
            ]
        },
        "captions": {
            "slots": [
                {"name": "title_hook", "anchor": "top_center", "is_hook": True, "appear_sec": 0.0, "duration_sec": 3.0}
            ]
        },
        "visual": {
            "color_grade": "warm_soft_aesthetic",
            "lighting": "natural_window_soft",
            "accent_color": "#ED99BE",
        },
        "pacing": {
            "cut_count_range": [4, 12],
            "avg_shot_len_sec": 1.5,
            "rhythm_mode": "mixed",
        },
    }


# ---------------------------------------------------------------------------
# build_brief — 기본 반환 키 검증
# ---------------------------------------------------------------------------

def test_build_brief_returns_all_required_keys(sample_profile):
    """build_brief는 규정된 모든 키를 포함하는 dict를 반환해야 한다."""
    ui = UserInput(kind="text", value="글로우 세럼")
    brief = build_brief(sample_profile, ui)

    required_keys = {"user_input", "narrative", "captions", "visual", "pacing", "run_dir", "profile_path"}
    assert required_keys.issubset(brief.keys()), (
        f"누락된 키: {required_keys - brief.keys()}"
    )


def test_build_brief_user_input_embedded(sample_profile):
    """brief["user_input"]은 kind와 value를 모두 포함해야 한다."""
    ui = UserInput(kind="text", value="스킨케어 신제품")
    brief = build_brief(sample_profile, ui)

    assert brief["user_input"]["kind"] == "text"
    assert brief["user_input"]["value"] == "스킨케어 신제품"


def test_build_brief_profile_sections_copied(sample_profile):
    """build_brief는 profile의 narrative/captions/visual/pacing을 brief에 포함해야 한다."""
    ui = UserInput(kind="text", value="테스트")
    brief = build_brief(sample_profile, ui)

    assert brief["narrative"] == sample_profile["narrative"]
    assert brief["captions"] == sample_profile["captions"]
    assert brief["visual"] == sample_profile["visual"]
    assert brief["pacing"] == sample_profile["pacing"]


def test_build_brief_profile_path_kwarg(sample_profile):
    """profile_path 키워드 인수가 brief에 기록되어야 한다."""
    ui = UserInput(kind="text", value="테스트")
    brief = build_brief(sample_profile, ui, profile_path="profiles/biodance.json")

    assert brief["profile_path"] == "profiles/biodance.json"


def test_build_brief_profile_path_defaults_to_empty(sample_profile):
    """profile_path를 전달하지 않으면 빈 문자열이어야 한다."""
    ui = UserInput(kind="text", value="테스트")
    brief = build_brief(sample_profile, ui)

    assert brief["profile_path"] == ""


def test_build_brief_run_dir_initially_empty(sample_profile):
    """build_brief 직후 run_dir은 빈 문자열이어야 한다 (caller가 설정)."""
    ui = UserInput(kind="text", value="테스트")
    brief = build_brief(sample_profile, ui)

    assert brief["run_dir"] == ""


# ---------------------------------------------------------------------------
# build_brief — text 입력 (파일 검증 없음)
# ---------------------------------------------------------------------------

def test_build_brief_text_input_no_file_check(sample_profile):
    """text kind는 파일 경로가 아니어도 InputError를 발생시키지 않아야 한다."""
    ui = UserInput(kind="text", value="존재하지않는파일.jpg")
    # text 타입이므로 파일 존재 여부 검증을 건너뜀
    brief = build_brief(sample_profile, ui)
    assert brief["user_input"]["kind"] == "text"


# ---------------------------------------------------------------------------
# build_brief — image 입력 검증
# ---------------------------------------------------------------------------

def test_build_brief_image_nonexistent_file_raises(sample_profile, tmp_path):
    """image kind에서 파일이 없으면 InputError를 발생시켜야 한다."""
    ui = UserInput(kind="image", value=str(tmp_path / "nonexistent.jpg"))
    with pytest.raises(InputError) as exc_info:
        build_brief(sample_profile, ui)
    assert exc_info.value.kind == "image"


def test_build_brief_image_unsupported_extension_raises(sample_profile, tmp_path):
    """image kind에서 지원하지 않는 확장자(bmp)는 InputError를 발생시켜야 한다."""
    bad_file = tmp_path / "photo.bmp"
    bad_file.write_bytes(b"\x00\x01")
    ui = UserInput(kind="image", value=str(bad_file))
    with pytest.raises(InputError) as exc_info:
        build_brief(sample_profile, ui)
    assert exc_info.value.kind == "image"


def test_build_brief_image_valid_jpg(sample_profile, tmp_path):
    """image kind에서 존재하는 .jpg 파일은 성공해야 한다."""
    img_file = tmp_path / "product.jpg"
    img_file.write_bytes(b"\xff\xd8\xff")  # minimal JPEG header
    ui = UserInput(kind="image", value=str(img_file))
    brief = build_brief(sample_profile, ui)
    assert brief["user_input"]["kind"] == "image"


def test_build_brief_image_valid_png(sample_profile, tmp_path):
    """image kind에서 존재하는 .png 파일은 성공해야 한다."""
    img_file = tmp_path / "product.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes
    ui = UserInput(kind="image", value=str(img_file))
    brief = build_brief(sample_profile, ui)
    assert brief["user_input"]["kind"] == "image"


# ---------------------------------------------------------------------------
# build_brief — video 입력 검증
# ---------------------------------------------------------------------------

def test_build_brief_video_nonexistent_file_raises(sample_profile, tmp_path):
    """video kind에서 파일이 없으면 InputError를 발생시켜야 한다."""
    ui = UserInput(kind="video", value=str(tmp_path / "clip.mp4"))
    with pytest.raises(InputError) as exc_info:
        build_brief(sample_profile, ui)
    assert exc_info.value.kind == "video"


def test_build_brief_video_unsupported_extension_raises(sample_profile, tmp_path):
    """video kind에서 지원하지 않는 확장자(avi)는 InputError를 발생시켜야 한다."""
    bad_file = tmp_path / "clip.avi"
    bad_file.write_bytes(b"\x52\x49\x46\x46")
    ui = UserInput(kind="video", value=str(bad_file))
    with pytest.raises(InputError) as exc_info:
        build_brief(sample_profile, ui)
    assert exc_info.value.kind == "video"


def test_build_brief_video_valid_mp4(sample_profile, tmp_path):
    """video kind에서 존재하는 .mp4 파일은 성공해야 한다."""
    vid_file = tmp_path / "clip.mp4"
    vid_file.write_bytes(b"\x00\x00\x00\x20ftyp")  # minimal mp4-like header
    ui = UserInput(kind="video", value=str(vid_file))
    brief = build_brief(sample_profile, ui)
    assert brief["user_input"]["kind"] == "video"


def test_build_brief_video_valid_mov(sample_profile, tmp_path):
    """video kind에서 존재하는 .mov 파일은 성공해야 한다."""
    vid_file = tmp_path / "clip.mov"
    vid_file.write_bytes(b"\x00\x00\x00\x08wide")
    ui = UserInput(kind="video", value=str(vid_file))
    brief = build_brief(sample_profile, ui)
    assert brief["user_input"]["kind"] == "video"


# ---------------------------------------------------------------------------
# write_prompt_txt
# ---------------------------------------------------------------------------

def test_write_prompt_txt_creates_file(sample_profile, tmp_path):
    """write_prompt_txt는 run_dir/prompt.txt를 생성해야 한다."""
    ui = UserInput(kind="text", value="글로우 세럼")
    brief = build_brief(sample_profile, ui, profile_path="profiles/biodance.json")
    brief["run_dir"] = str(tmp_path)

    write_prompt_txt(brief, "이거 진짜 달라졌어 ✨", str(tmp_path))

    prompt_file = tmp_path / "prompt.txt"
    assert prompt_file.exists(), "prompt.txt 파일이 생성되어야 합니다"


def test_write_prompt_txt_contains_user_input_value(sample_profile, tmp_path):
    """prompt.txt는 user_input value를 포함해야 한다."""
    ui = UserInput(kind="text", value="글로우 세럼 신제품")
    brief = build_brief(sample_profile, ui)
    brief["run_dir"] = str(tmp_path)

    write_prompt_txt(brief, "피부가 달라졌어요", str(tmp_path))

    content = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    assert "글로우 세럼 신제품" in content


def test_write_prompt_txt_contains_hook_text(sample_profile, tmp_path):
    """prompt.txt는 hook_text를 포함해야 한다."""
    ui = UserInput(kind="text", value="테스트 제품")
    brief = build_brief(sample_profile, ui)
    brief["run_dir"] = str(tmp_path)

    hook = "이거 진짜 달라졌어 ✨"
    write_prompt_txt(brief, hook, str(tmp_path))

    content = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    assert hook in content


def test_write_prompt_txt_contains_profile_path(sample_profile, tmp_path):
    """prompt.txt는 profile_path를 포함해야 한다."""
    ui = UserInput(kind="text", value="테스트")
    brief = build_brief(sample_profile, ui, profile_path="profiles/biodance.json")
    brief["run_dir"] = str(tmp_path)

    write_prompt_txt(brief, "훅 텍스트", str(tmp_path))

    content = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    assert "profiles/biodance.json" in content


def test_write_prompt_txt_contains_timestamp(sample_profile, tmp_path):
    """prompt.txt는 ISO 형식 타임스탬프를 포함해야 한다."""
    ui = UserInput(kind="text", value="테스트")
    brief = build_brief(sample_profile, ui)
    brief["run_dir"] = str(tmp_path)

    write_prompt_txt(brief, "훅", str(tmp_path))

    content = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    # 타임스탬프 형식 YYYY-MM-DD HH:MM:SS 확인
    import re
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content), (
        "prompt.txt에 타임스탬프(YYYY-MM-DD HH:MM:SS 형식)가 포함되어야 합니다"
    )


def test_write_prompt_txt_creates_parent_dir(sample_profile, tmp_path):
    """write_prompt_txt는 run_dir이 없어도 자동으로 디렉터리를 생성해야 한다."""
    ui = UserInput(kind="text", value="테스트")
    brief = build_brief(sample_profile, ui)

    nested_dir = str(tmp_path / "outputs" / "run_20240101_120000_abc123")
    brief["run_dir"] = nested_dir

    write_prompt_txt(brief, "훅", nested_dir)

    prompt_file = Path(nested_dir) / "prompt.txt"
    assert prompt_file.exists()

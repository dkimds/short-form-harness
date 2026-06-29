#!/usr/bin/env python3
"""cli.py — 숏폼 영상 생성 하네스 CLI

서브커맨드:
  analyze   레퍼런스 mp4 1개를 분석해 style_profile.json 생성
  generate  style_profile.json + 사용자 입력으로 숏폼 영상 생성 (미구현)

사용 예시:
  uv run python cli.py analyze --ref refs/reference1.mp4 --out profiles/ref1.json
  uv run python cli.py generate --profile profiles/ref1.json --input "텍스트" --runs 1
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from src.analyze.audio_stats import analyze_audio
from src.analyze.cut_detect import compute_pacing_metrics, detect_cuts
from src.analyze.probe import probe, to_format_section
from src.analyze.synthesize_profile import save_profile, synthesize
from src.analyze.vision import analyze_vision
from src.common.config import Config, load_config
from src.common.exceptions import ConfigError, InputError, ProfileValidationError, UnprocessableRefError
from src.common.io import make_run_id, make_run_dir, read_json
from src.common.vendor_client import VendorClient
from src.generate.brief import UserInput, build_brief, write_prompt_txt
from src.generate.hook_gen import fill_hook_slot, generate_hook
from src.generate.plan import build_shotlist, write_shotlist
from src.generate.assets import render_assets
from src.generate.compose import compose_video

logger = logging.getLogger(__name__)


def cmd_analyze(args: argparse.Namespace, config: Config, client: VendorClient) -> None:
    """analyze 서브커맨드 실행.

    --ref 로 받은 mp4 파일 1개에 대해 probe → cut_detect → audio_stats → vision 순으로
    분석을 수행하고, 결과를 --out 경로에 style_profile.json 으로 저장한다.

    Args:
        args: argparse 파싱 결과. args.ref, args.out 을 사용한다.
        config: 환경 변수에서 로드한 설정 객체.
        client: Google API 격리 래퍼.
    """
    ref_path: str = args.ref
    out_path: str = args.out

    logger.info("레퍼런스 분석 시작: %s", ref_path)

    # ── 1. probe ──────────────────────────────────────────────────────────
    try:
        probe_result = probe(ref_path)
    except UnprocessableRefError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)

    # ── 2. cut_detect + pacing ────────────────────────────────────────────
    cuts = detect_cuts(ref_path)
    pacing = compute_pacing_metrics(cuts, probe_result.duration_sec)
    logger.info(
        "페이싱 지표 — 컷 수: %d, 평균 숏 길이: %.2fs, 훅 밀도: %s",
        pacing["cut_count"],
        pacing["avg_shot_len_sec"],
        pacing["hook_cut_density"],
    )

    # ── 3. audio_stats ────────────────────────────────────────────────────
    audio = analyze_audio(ref_path)
    audio_dict = asdict(audio)
    logger.info(
        "오디오 — music_start: %.2fs, LUFS: %.1f, VO: %s",
        audio.music_start_sec,
        audio.target_lufs,
        audio.has_voiceover,
    )

    # ── 4. vision ─────────────────────────────────────────────────────────
    vision_dict = analyze_vision(client, ref_path)
    if vision_dict is None:
        logger.warning("비전 분석 결과 없음: %s — 빈 dict로 계속 진행합니다.", ref_path)
        vision_dict = {}
    else:
        logger.info("비전 분석 완료: %s", ref_path)

    # ── format 섹션 ───────────────────────────────────────────────────────
    probe_data = to_format_section([probe_result])

    # ── 프로파일 합성 ──────────────────────────────────────────────────────
    profile = synthesize(
        probe_data,
        pacing,
        audio_dict,
        vision_dict,
        source_refs=[ref_path],
        extracted_by=config.gemini_model,
    )

    # ── 저장 (스키마 검증 포함) ────────────────────────────────────────────
    try:
        save_profile(profile, out_path)
    except ProfileValidationError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ 프로파일이 저장되었습니다: {out_path}")


def cmd_generate(args: argparse.Namespace, config: Config, client: VendorClient) -> None:
    """generate 서브커맨드 — 숏폼 영상 생성 파이프라인.

    Pipeline:
        build_brief → generate_hook → fill_hook_slot
        → build_shotlist → render_assets → compose_video
        → write_prompt_txt → write_shotlist

    Args:
        args: argparse 파싱 결과. args.profile, args.input, args.runs 를 사용한다.
        config: 설정 객체 (현재 미사용).
        client: API 래퍼.
    """
    import random

    # ── 1. 프로파일 로드 ────────────────────────────────────────────────────
    try:
        profile = read_json(args.profile)
    except FileNotFoundError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        sys.exit(1)

    # ── 2. 입력 타입 감지 ───────────────────────────────────────────────────
    input_value: str = args.input
    lower = input_value.lower()
    if lower.endswith((".jpg", ".png")):
        kind = "image"
    elif lower.endswith((".mp4", ".mov")):
        kind = "video"
    else:
        kind = "text"

    # ── 3. UserInput + build_brief ─────────────────────────────────────────
    user_input = UserInput(kind=kind, value=input_value)
    try:
        brief = build_brief(profile, user_input, profile_path=args.profile)
    except InputError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        sys.exit(1)

    runs: int = args.runs
    completed = 0

    for run_idx in range(runs):
        run_id = make_run_id()
        run_dir = make_run_dir(run_id)
        brief["run_dir"] = run_dir

        try:
            # ── 4. 훅 생성 ────────────────────────────────────────────────
            hook_text = generate_hook(client, brief, profile)

            # ── 5. 훅 슬롯 채우기 ─────────────────────────────────────────
            profile_with_hook = fill_hook_slot(profile, hook_text)

            # ── 6. 숏리스트 생성 ──────────────────────────────────────────
            shotlist = build_shotlist(
                brief,
                profile_with_hook,
                hook_text,
                rng=random.Random(),
            )

            # ── 7. 에셋 생성 ──────────────────────────────────────────────
            shotlist = render_assets(client, shotlist, profile_with_hook, run_dir)

            # ── 8. 영상 합성 ──────────────────────────────────────────────
            final_mp4_path = compose_video(shotlist, profile_with_hook, run_dir)

            # ── 9. 결과물 기록 ────────────────────────────────────────────
            write_prompt_txt(brief, hook_text, run_dir)
            write_shotlist(shotlist, run_dir)

            prompt_txt_path = str(Path(run_dir) / "prompt.txt")
            completed += 1

            print(
                f"\n✅ Run {run_idx + 1}/{runs} 완료\n"
                f"   run_id    : {run_id}\n"
                f"   final.mp4 : {final_mp4_path}\n"
                f"   prompt.txt: {prompt_txt_path}"
            )

        except InputError as exc:
            print(f"입력 오류 (run {run_idx + 1}): {exc}", file=sys.stderr)
            if runs == 1:
                sys.exit(1)
            else:
                logger.warning("[generate] run %d/%d 실패 (InputError): %s", run_idx + 1, runs, exc)
                continue

        except Exception as exc:  # HarnessError and other unexpected errors
            if runs == 1:
                print(f"오류: {exc}", file=sys.stderr)
                sys.exit(1)
            else:
                logger.warning("[generate] run %d/%d 실패: %s", run_idx + 1, runs, exc)
                continue

    if runs > 1:
        print(f"\n총 {completed}/{runs} 개 run 완료.")


def _build_parser() -> argparse.ArgumentParser:
    """argparse 파서를 구성하고 반환한다."""
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="숏폼 영상 생성 하네스 — 레퍼런스를 분석하고 새 영상을 생성합니다.",
    )
    subparsers = parser.add_subparsers(
        dest="subcommand",
        metavar="SUBCOMMAND",
    )
    subparsers.required = True  # 서브커맨드 필수

    # ── analyze 서브커맨드 ─────────────────────────────────────────────────
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="레퍼런스 mp4 1개를 분석해 style_profile.json을 생성합니다.",
        description=(
            "레퍼런스 mp4 파일 1개를 분석해 페이싱·오디오·비전 정보를 추출하고\n"
            "style_profile.json 파일을 생성합니다.\n\n"
            "예시:\n"
            "  uv run python cli.py analyze --ref refs/reference1.mp4 --out profiles/ref1.json\n"
            "  uv run python cli.py analyze --ref refs/reference2.mp4 --out profiles/ref2.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyze_parser.add_argument(
        "--ref",
        required=True,
        metavar="MP4",
        help="분석할 레퍼런스 mp4 파일 경로.",
    )
    analyze_parser.add_argument(
        "--out",
        required=True,
        metavar="JSON",
        help="출력할 style_profile.json 경로 (예: profiles/biodance.json).",
    )

    # ── generate 서브커맨드 ────────────────────────────────────────────────
    generate_parser = subparsers.add_parser(
        "generate",
        help="style_profile.json과 사용자 입력으로 숏폼 영상을 생성합니다. (미구현)",
        description=(
            "style_profile.json과 사용자 입력(텍스트/이미지/영상)을 받아\n"
            "같은 스타일의 새 숏폼 mp4를 생성합니다.\n\n"
            "예시:\n"
            "  uv run python cli.py generate --profile profiles/biodance.json --input \"글로우 세럼\" --runs 1\n"
            "  uv run python cli.py generate --profile profiles/biodance.json --input image.png"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    generate_parser.add_argument(
        "--profile",
        required=True,
        metavar="JSON",
        help="analyze 단계에서 생성한 style_profile.json 경로.",
    )
    generate_parser.add_argument(
        "--input",
        required=True,
        metavar="TEXT_OR_PATH",
        help="생성 입력. 텍스트 문자열 또는 이미지/영상 파일 경로.",
    )
    generate_parser.add_argument(
        "--runs",
        type=int,
        default=1,
        metavar="N",
        help="동일 입력으로 반복 생성할 횟수 (기본값: 1).",
    )

    return parser


def main() -> None:
    """CLI 진입점.

    1. 로깅 초기화
    2. argparse로 서브커맨드·인수 파싱
    3. load_config() 호출 — ConfigError 시 안내 메시지 출력 후 sys.exit(1)
    4. VendorClient 생성
    5. 서브커맨드 함수 호출
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = _build_parser()
    args = parser.parse_args()

    # 설정 로드 — GOOGLE_API_KEY 미설정 시 ConfigError
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        sys.exit(1)

    client = VendorClient(config)

    # 서브커맨드 디스패치
    if args.subcommand == "analyze":
        cmd_analyze(args, config, client)
    elif args.subcommand == "generate":
        cmd_generate(args, config, client)
    else:
        # argparse가 required=True를 강제하므로 실제로는 도달하지 않음
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

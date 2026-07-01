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
from src.generate.plan import build_shotlist, normalize_profile_duration, write_shotlist
from src.generate.assets import render_assets
from src.generate.compose import compose_video, get_music_duration
from src.generate.gate import run_gate

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

    # ── 1.5 배경(setting) override (선택) ───────────────────────────────────
    # --background는 프로파일의 visual.setting을 텍스트 프롬프트로 덮어쓴다.
    # visual 섹션 전체(色감·조명 등)는 그대로 두고 setting만 교체한다.
    if args.background:
        profile.setdefault("visual", {})["setting"] = args.background

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
    creator_photo_path: str | None = args.creator_photo
    try:
        brief = build_brief(
            profile,
            user_input,
            profile_path=args.profile,
            creator_photo_path=creator_photo_path,
        )
    except InputError as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        sys.exit(1)

    # 크리에이터 사진 바이트를 미리 로드 (매 run에서 재사용)
    creator_photo_bytes: bytes | None = None
    if creator_photo_path:
        creator_photo_bytes = Path(creator_photo_path).read_bytes()

    # ── 3.5 목표 재생 시간 결정 ──────────────────────────────────────────────
    # --duration 미지정 시, 음악 트랙의 실제 길이(music_start_sec 오프셋 제외)에
    # 맞춘다 — 음악이 루프 없이 정확히 한 번 재생되고 끝나도록. 15초 숏폼 하한은
    # 권장값일 뿐 필수가 아니므로 enforce_min=False로 하한 클램프를 건너뛴다.
    # 음악 길이를 알 수 없으면(트랙/moviepy 로드 실패) 기존 15~60초 클램프로 폴백.
    target_duration_sec: float | None = args.duration
    enforce_min_duration = True
    if target_duration_sec is None:
        audio_cfg = profile.get("audio", {})
        music_duration = get_music_duration(
            audio_cfg.get("music_mood", ""),
            music_start_sec=float(audio_cfg.get("music_start_sec", 0.0)),
        )
        if music_duration is not None:
            target_duration_sec = music_duration
            enforce_min_duration = False
            logger.info(
                "[generate] --duration 미지정 — 음악 길이(%.2fs)에 재생시간을 맞춥니다.",
                music_duration,
            )
        else:
            logger.warning(
                "[generate] 음악 길이를 확인할 수 없어 기존 15~60초 클램프로 폴백합니다."
            )

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

            # ── 5.5 재생 시간 정규화 ──────────────────────────────────────
            profile_with_hook = normalize_profile_duration(
                profile_with_hook,
                target_sec=target_duration_sec,
                enforce_min=enforce_min_duration,
            )

            # ── 6. 숏리스트 생성 ──────────────────────────────────────────
            shotlist = build_shotlist(
                brief,
                profile_with_hook,
                hook_text,
                rng=random.Random(),
            )

            # ── 7. 에셋 생성 ──────────────────────────────────────────────
            shotlist = render_assets(
                client,
                shotlist,
                profile_with_hook,
                run_dir,
                creator_photo=creator_photo_bytes,
            )

            # ── 8. 영상 합성 ──────────────────────────────────────────────
            final_mp4_path = compose_video(shotlist, profile_with_hook, run_dir)

            # ── 9. 결과물 기록 ────────────────────────────────────────────
            write_prompt_txt(brief, hook_text, run_dir)
            write_shotlist(shotlist, run_dir)

            # ── 10. Gate QA 판정 ──────────────────────────────────────────
            gate_result = run_gate(client, final_mp4_path, profile_with_hook, run_dir)
            gate_status = "PASS" if gate_result.passed else "FAIL"

            prompt_txt_path = str(Path(run_dir) / "prompt.txt")
            gate_json_path = str(Path(run_dir) / "gate.json")
            completed += 1

            print(
                f"\n✅ Run {run_idx + 1}/{runs} 완료\n"
                f"   run_id    : {run_id}\n"
                f"   final.mp4 : {final_mp4_path}\n"
                f"   prompt.txt: {prompt_txt_path}\n"
                f"   gate      : {gate_status} → {gate_json_path}"
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
            "  uv run python cli.py generate --profile profiles/ref1.json --input \"글로우 세럼\" --runs 1\n"
            "  uv run python cli.py generate --profile profiles/ref1.json --input image.png\n"
            "  uv run python cli.py generate --profile profiles/ref1.json --input \"글로우 세럼\" \\\n"
            "    --creator-photo creator.jpg --background \"minimalist white studio\""
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
        "--creator-photo",
        default=None,
        metavar="IMAGE",
        help=(
            "크리에이터(인물) 참조 사진 경로 (선택, jpg/jpeg/png). "
            "주어지면 hook·application 장면 생성 시 인물 일관성 유지에 사용됩니다."
        ),
    )
    generate_parser.add_argument(
        "--background",
        default=None,
        metavar="TEXT",
        help=(
            "배경 묘사 텍스트 (선택). 주어지면 프로파일의 visual.setting을 "
            "덮어써 모든 장면 프롬프트의 Setting 절에 반영됩니다. "
            "예: --background \"minimalist white studio\""
        ),
    )
    generate_parser.add_argument(
        "--runs",
        type=int,
        default=1,
        metavar="N",
        help="동일 입력으로 반복 생성할 횟수 (기본값: 1).",
    )
    generate_parser.add_argument(
        "--duration",
        type=float,
        default=None,
        metavar="SEC",
        help=(
            "목표 재생 시간(초). 미지정 시 profile.audio.music_mood로 선택될 "
            "음악 트랙의 실제 길이(music_start_sec 오프셋 제외)에 맞춥니다 — "
            "음악이 루프 없이 정확히 한 번 재생되고 끝나도록. 15초 미만이어도 "
            "허용합니다(숏폼 15~60초는 권장값, 필수 제약 아님). 60초 상한만 "
            "안전장치로 유지합니다. 음악 길이를 알 수 없으면 기존 방식(15~60초 "
            "클램프)으로 폴백합니다."
        ),
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

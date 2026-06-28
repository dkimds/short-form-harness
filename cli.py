#!/usr/bin/env python3
"""cli.py — 숏폼 영상 생성 하네스 CLI

서브커맨드:
  analyze   레퍼런스 mp4를 분석해 style_profile.json 생성
  generate  style_profile.json + 사용자 입력으로 숏폼 영상 생성 (미구현)

사용 예시:
  uv run python cli.py analyze --refs ref1.mp4 ref2.mp4 --out profiles/biodance.json
  uv run python cli.py generate --profile profiles/biodance.json --input "텍스트" --runs 1
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import asdict

from src.analyze.audio_stats import analyze_audio
from src.analyze.cut_detect import compute_pacing_metrics, detect_cuts, merge_pacing
from src.analyze.probe import ProbeResult, probe, to_format_section
from src.analyze.synthesize_profile import save_profile, synthesize
from src.analyze.vision import analyze_vision
from src.common.config import Config, load_config
from src.common.exceptions import ConfigError, ProfileValidationError, UnprocessableRefError
from src.common.vendor_client import VendorClient

logger = logging.getLogger(__name__)


def cmd_analyze(args: argparse.Namespace, config: Config, client: VendorClient) -> None:
    """analyze 서브커맨드 실행.

    --refs 로 받은 각 mp4 파일에 대해 probe → cut_detect → audio_stats → vision 순으로
    분석을 수행하고, 결과를 통합해 --out 경로에 style_profile.json 을 저장한다.

    Args:
        args: argparse 파싱 결과. args.refs, args.out 을 사용한다.
        config: 환경 변수에서 로드한 설정 객체.
        client: Google API 격리 래퍼.
    """
    ref_paths: list[str] = args.refs
    out_path: str = args.out

    # 레퍼런스별 수집 버킷
    valid_probe_results: list[ProbeResult] = []
    per_ref_pacing: list[dict] = []
    audio_stats_list: list[dict] = []
    vision_results: list[dict | None] = []

    for path in ref_paths:
        logger.info("레퍼런스 분석 시작: %s", path)

        # ── 1. probe ──────────────────────────────────────────────────────
        try:
            probe_result = probe(path)
        except UnprocessableRefError as exc:
            logger.warning("레퍼런스 건너뜀 (probe 실패): %s — %s", path, exc)
            continue

        valid_probe_results.append(probe_result)

        # ── 2. cut_detect + pacing ────────────────────────────────────────
        cuts = detect_cuts(path)
        pacing_metrics = compute_pacing_metrics(cuts, probe_result.duration_sec)
        per_ref_pacing.append(pacing_metrics)
        logger.info(
            "페이싱 지표 — 컷 수: %d, 평균 숏 길이: %.2fs, 훅 밀도: %s",
            pacing_metrics["cut_count"],
            pacing_metrics["avg_shot_len_sec"],
            pacing_metrics["hook_cut_density"],
        )

        # ── 3. audio_stats ────────────────────────────────────────────────
        audio = analyze_audio(path)
        audio_stats_list.append(asdict(audio))
        logger.info(
            "오디오 — music_start: %.2fs, LUFS: %.1f, VO: %s",
            audio.music_start_sec,
            audio.target_lufs,
            audio.has_voiceover,
        )

        # ── 4. vision ─────────────────────────────────────────────────────
        vision = analyze_vision(client, path)
        if vision is None:
            logger.warning("비전 분석 결과 없음 (건너뜀): %s", path)
        else:
            logger.info("비전 분석 완료: %s", path)
        vision_results.append(vision)

    # ── 모든 레퍼런스 실패 시 종료 ─────────────────────────────────────────
    if not valid_probe_results:
        print(
            "오류: 유효한 레퍼런스 파일이 없습니다. "
            "--refs 로 전달한 모든 파일을 처리할 수 없었습니다.",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info(
        "%d/%d 레퍼런스 처리 완료. 프로파일 합성 중...",
        len(valid_probe_results),
        len(ref_paths),
    )

    # ── 결과 집계 ─────────────────────────────────────────────────────────

    # a) format 섹션
    probe_data = to_format_section(valid_probe_results)

    # b) pacing 병합
    pacing = merge_pacing(per_ref_pacing)

    # c) 오디오 평균
    #    - music_start_sec, target_lufs: 평균값
    #    - has_voiceover: 하나라도 True 이면 True
    n = len(audio_stats_list)
    avg_music_start = sum(a["music_start_sec"] for a in audio_stats_list) / n
    avg_target_lufs = sum(a["target_lufs"] for a in audio_stats_list) / n
    any_voiceover = any(a["has_voiceover"] for a in audio_stats_list)
    audio_dict: dict = {
        "music_start_sec": avg_music_start,
        "target_lufs": avg_target_lufs,
        "has_voiceover": any_voiceover,
    }

    # d) vision 병합: 첫 번째 non-None 결과 사용, 없으면 빈 dict
    vision_dict: dict = {}
    for v in vision_results:
        if v is not None:
            vision_dict = v
            break

    # ── 프로파일 합성 ──────────────────────────────────────────────────────
    profile = synthesize(
        probe_data,
        pacing,
        audio_dict,
        vision_dict,
        source_refs=ref_paths,
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
    """generate 서브커맨드 — 미구현 스텁.

    Args:
        args: argparse 파싱 결과.
        config: 설정 객체 (현재 미사용).
        client: API 래퍼 (현재 미사용).
    """
    print("generate 서브커맨드는 아직 구현되지 않았습니다.")


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
        help="레퍼런스 mp4를 분석해 style_profile.json을 생성합니다.",
        description=(
            "하나 이상의 레퍼런스 mp4 파일을 분석해 페이싱·오디오·비전 정보를 추출하고\n"
            "style_profile.json 파일을 생성합니다.\n\n"
            "예시:\n"
            "  uv run python cli.py analyze --refs ref1.mp4 ref2.mp4 --out profiles/biodance.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyze_parser.add_argument(
        "--refs",
        nargs="+",
        required=True,
        metavar="MP4",
        help="분석할 레퍼런스 mp4 파일 경로. 하나 이상 지정 가능.",
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

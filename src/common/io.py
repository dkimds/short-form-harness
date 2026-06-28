"""
src/common/io.py — run_id 생성 및 출력 디렉터리 관리

요구사항: 16.1, 16.2
"""

import json
import secrets
from datetime import datetime
from pathlib import Path


def make_run_id() -> str:
    """타임스탬프 기반 고유 run_id를 생성한다.

    형식: YYYYMMDD_HHMMSS_<6자 랜덤 hex>
    datetime.now()와 secrets.token_hex(3)를 조합해 디렉터리 이름으로 안전한
    문자열을 반환한다.

    Returns:
        str: e.g. "20240615_143022_a1b2c3"
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = secrets.token_hex(3)  # 6자 hex (3 bytes)
    return f"{timestamp}_{suffix}"


def make_run_dir(run_id: str, base_dir: str = "outputs") -> str:
    """run_id에 해당하는 출력 디렉터리를 생성하고 절대 경로를 반환한다.

    <base_dir>/<run_id>/ 디렉터리와 그 상위 디렉터리를 모두 생성한다.

    Args:
        run_id: make_run_id()로 생성된 고유 식별자
        base_dir: 기본 출력 루트 디렉터리 (기본값: "outputs")

    Returns:
        str: 생성된 디렉터리의 절대 경로
    """
    run_dir = Path(base_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return str(run_dir.resolve())


def write_json(data: dict, path: str | Path) -> None:
    """dict를 JSON 파일로 저장한다.

    indent=2, ensure_ascii=False로 직렬화해 한글 등 비ASCII 문자를
    그대로 보존한다.

    Args:
        data: 저장할 딕셔너리
        path: 저장 경로 (str 또는 Path)
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def read_json(path: str | Path) -> dict:
    """JSON 파일을 읽어 dict로 반환한다.

    Args:
        path: 읽을 파일 경로 (str 또는 Path)

    Returns:
        dict: 파싱된 JSON 데이터

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때,
            어떤 경로가 없는지 안내 메시지 포함
    """
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(
            f"JSON 파일을 찾을 수 없습니다: {src.resolve()}\n"
            "경로가 올바른지, 파일이 생성되었는지 확인하세요."
        )
    return json.loads(src.read_text(encoding="utf-8"))

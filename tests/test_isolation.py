"""
tests/test_isolation.py

Task 9.1 — Property 18: 단계 import 격리와 벤더 호출 단일화
Task 9.2 — Property 19: 생성 단계는 레퍼런스 미디어를 참조하지 않는다
Task 9.3 — 리포 위생 스모크 테스트

**Validates: Requirements 13.1, 13.2, 13.5, 10.7, 11.9, 17.1, 15.1, 15.4, 15.5**
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# 프로젝트 루트 (tests/ → project root)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ANALYZE = PROJECT_ROOT / "src" / "analyze"
SRC_GENERATE = PROJECT_ROOT / "src" / "generate"
VENDOR_CLIENT = PROJECT_ROOT / "src" / "common" / "vendor_client.py"


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _py_files(directory: Path) -> list[Path]:
    """디렉터리 아래 __pycache__를 제외한 모든 .py 파일 목록을 반환한다."""
    return [
        p for p in directory.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def _collect_imports(source: str) -> list[tuple[str, str | None]]:
    """소스 코드에서 모든 import 문을 (module, from_module) 튜플로 수집한다.

    - ``import foo.bar``   → ("foo.bar", None)
    - ``from foo import x`` → ("foo", "foo")
    Returns list of (module_string, from_prefix_or_none).
    """
    tree = ast.parse(source)
    results: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, None))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            results.append((module, module))
    return results


# ===========================================================================
# Task 9.1 — Property 18: 단계 import 격리와 벤더 호출 단일화
# ===========================================================================

class TestImportIsolation:
    """src/analyze/ ↔ src/generate/ 가 서로 import하지 않는지,
    Google SDK를 vendor_client.py 외에서 직접 import하지 않는지 검증한다.

    **Validates: Requirements 13.1, 13.2, 13.5**
    """

    def test_analyze_does_not_import_generate(self) -> None:
        """src/analyze/ 의 모든 .py 파일이 src.generate 를 import하지 않는다.

        **Validates: Requirements 13.1, 13.2**
        """
        violations: list[str] = []
        for py_file in _py_files(SRC_ANALYZE):
            source = py_file.read_text(encoding="utf-8")
            imports = _collect_imports(source)
            for module, _ in imports:
                if module.startswith("src.generate"):
                    violations.append(f"{py_file.relative_to(PROJECT_ROOT)}: imports '{module}'")

        assert not violations, (
            "src/analyze/ 모듈이 src.generate 를 import하고 있습니다 (요구사항 13.1, 13.2):\n"
            + "\n".join(f"  • {v}" for v in violations)
        )

    def test_generate_does_not_import_analyze(self) -> None:
        """src/generate/ 의 모든 .py 파일이 src.analyze 를 import하지 않는다.

        **Validates: Requirements 13.1, 13.2**
        """
        violations: list[str] = []
        for py_file in _py_files(SRC_GENERATE):
            source = py_file.read_text(encoding="utf-8")
            imports = _collect_imports(source)
            for module, _ in imports:
                if module.startswith("src.analyze"):
                    violations.append(f"{py_file.relative_to(PROJECT_ROOT)}: imports '{module}'")

        assert not violations, (
            "src/generate/ 모듈이 src.analyze 를 import하고 있습니다 (요구사항 13.1, 13.2):\n"
            + "\n".join(f"  • {v}" for v in violations)
        )

    def test_only_vendor_client_imports_google_sdk(self) -> None:
        """src/analyze/ 와 src/generate/ 의 모든 .py 파일이 Google SDK를
        직접 import하지 않는다. 허용: src/common/vendor_client.py 만.

        금지 패턴: 'google.generativeai' (구 SDK), 'google.genai' (신 SDK)

        **Validates: Requirements 13.5**
        """
        # 금지 최상위 모듈 패턴
        FORBIDDEN_SDK_PREFIXES = ("google.generativeai", "google.genai")

        violations: list[str] = []
        all_checked_files = _py_files(SRC_ANALYZE) + _py_files(SRC_GENERATE)

        for py_file in all_checked_files:
            source = py_file.read_text(encoding="utf-8")
            imports = _collect_imports(source)
            for module, _ in imports:
                for prefix in FORBIDDEN_SDK_PREFIXES:
                    if module == prefix or module.startswith(prefix + "."):
                        violations.append(
                            f"{py_file.relative_to(PROJECT_ROOT)}: "
                            f"직접 Google SDK import '{module}'"
                        )

        assert not violations, (
            "vendor_client.py 외의 모듈이 Google SDK를 직접 import하고 있습니다 (요구사항 13.5).\n"
            "모든 벤더 호출은 src/common/vendor_client.py 를 통해야 합니다:\n"
            + "\n".join(f"  • {v}" for v in violations)
        )


# ===========================================================================
# Task 9.2 — Property 19: 생성 단계는 레퍼런스 미디어를 참조하지 않는다
# ===========================================================================

class TestGenerateNoRefMedia:
    """src/generate/ 의 어떤 파일도 레퍼런스 미디어 경로를 참조하지 않는다.

    **Validates: Requirements 10.7, 11.9, 13.3, 17.1**
    """

    def test_no_refs_path_string(self) -> None:
        """src/generate/ 의 모든 .py 파일에 'refs/' 경로 문자열이 없다.

        **Validates: Requirements 10.7, 11.9, 17.1**
        """
        violations: list[str] = []
        for py_file in _py_files(SRC_GENERATE):
            source = py_file.read_text(encoding="utf-8")
            # "refs/"를 포함하는 줄 탐색
            for lineno, line in enumerate(source.splitlines(), start=1):
                if "refs/" in line:
                    violations.append(
                        f"{py_file.relative_to(PROJECT_ROOT)}:{lineno}: {line.strip()}"
                    )

        assert not violations, (
            "src/generate/ 모듈이 레퍼런스 경로 'refs/'를 포함하고 있습니다 (요구사항 10.7, 11.9):\n"
            + "\n".join(f"  • {v}" for v in violations)
        )

    def test_source_refs_not_used_in_file_open(self) -> None:
        """src/generate/ 의 모든 .py 파일에서 source_refs 가 open()/파일 읽기
        컨텍스트에 사용되지 않는다.

        허용: dict 키 문자열(예: ``data["source_refs"]``)
        금지: open(source_refs, ...), Path(source_refs).read_*,
              read_text(source_refs), read_bytes(source_refs) 등 파일 I/O 컨텍스트

        **Validates: Requirements 10.7, 13.3, 17.1**
        """
        # 파일 I/O 컨텍스트에서 source_refs 사용을 탐지하는 패턴들
        FILE_IO_PATTERNS = [
            # open(source_refs  또는  open(...source_refs...
            re.compile(r"\bopen\s*\([^)]*source_refs"),
            # Path(source_refs)
            re.compile(r"\bPath\s*\([^)]*source_refs"),
            # .read_text(  또는  .read_bytes( 호출 체인 앞에 source_refs
            re.compile(r"\bsource_refs\s*[\.\[].*\bread_(?:text|bytes)\b"),
            # source_refs 변수에서 직접 read_* 호출
            re.compile(r"\bsource_refs\b.*\.read_(?:text|bytes)\s*\("),
        ]

        violations: list[str] = []
        for py_file in _py_files(SRC_GENERATE):
            source = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                for pattern in FILE_IO_PATTERNS:
                    if pattern.search(line):
                        violations.append(
                            f"{py_file.relative_to(PROJECT_ROOT)}:{lineno}: {line.strip()}"
                        )

        assert not violations, (
            "src/generate/ 모듈이 source_refs를 파일 I/O 컨텍스트에서 사용하고 있습니다 "
            "(요구사항 10.7, 13.3, 17.1):\n"
            + "\n".join(f"  • {v}" for v in violations)
        )


# ===========================================================================
# Task 9.3 — 리포 위생 스모크 테스트
# ===========================================================================

class TestRepoHygiene:
    """프로젝트 루트의 .env.example, .gitignore, 소스 파일에 시크릿이
    없음을 검증하는 스모크 테스트.

    **Validates: Requirements 15.1, 15.4, 15.5**
    """

    def test_env_example_exists(self) -> None:
        """.env.example 파일이 프로젝트 루트에 존재한다.

        **Validates: Requirements 15.1**
        """
        env_example = PROJECT_ROOT / ".env.example"
        assert env_example.exists(), (
            f".env.example 파일이 존재하지 않습니다: {env_example}\n"
            "  해결: 프로젝트 루트에 .env.example 파일을 생성하세요."
        )

    def test_env_example_has_no_real_api_key(self) -> None:
        """.env.example 의 GOOGLE_API_KEY= 라인에 실제 키 값이 없다.

        실제 키 판정: 값이 비어있지 않으면서 '<' 또는 'your'(대소문자 무관)
        를 포함하지 않는 경우.

        **Validates: Requirements 15.4, 15.5**
        """
        env_example = PROJECT_ROOT / ".env.example"
        if not env_example.exists():
            return  # test_env_example_exists 에서 이미 실패

        content = env_example.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("GOOGLE_API_KEY="):
                continue
            # GOOGLE_API_KEY= 뒤의 값 추출
            value = stripped[len("GOOGLE_API_KEY="):].strip()
            if not value:
                # 빈 값 → OK (예시 파일의 올바른 상태)
                continue
            # placeholder 패턴 확인: '<' 또는 'your' 포함 → OK
            lower_value = value.lower()
            if "<" in lower_value or "your" in lower_value:
                continue
            # 나머지는 실제 키로 간주
            assert False, (
                f".env.example 의 {lineno}번째 줄에 실제 API 키 값이 있는 것으로 보입니다 "
                f"(요구사항 15.4, 15.5).\n"
                f"  해결: GOOGLE_API_KEY= 뒤의 값을 비우거나 <your-key-here> 형태의 "
                f"placeholder로 교체하세요."
            )

    def test_gitignore_contains_dotenv(self) -> None:
        """.gitignore 에 .env 항목이 포함되어 있다.

        **Validates: Requirements 15.1, 15.5**
        """
        gitignore = PROJECT_ROOT / ".gitignore"
        assert gitignore.exists(), (
            f".gitignore 파일이 존재하지 않습니다: {gitignore}"
        )

        content = gitignore.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines()]

        # '.env' 가 독립된 줄(정확한 항목)로 존재하는지 확인
        assert ".env" in lines, (
            ".gitignore 에 '.env' 항목이 없습니다 (요구사항 15.1, 15.5).\n"
            "  해결: .gitignore 에 '.env' 줄을 추가하세요."
        )

    def test_no_hardcoded_api_key_in_src(self) -> None:
        """src/ 아래 어떤 Python 파일도 실제 GOOGLE_API_KEY 값을 하드코딩하지 않는다.

        탐지 패턴: GOOGLE_API_KEY= 또는 GOOGLE_API_KEY = 뒤에
        따옴표로 감싸진 비-placeholder 값이 오는 경우.

        **Validates: Requirements 15.4, 15.5**
        """
        SRC_DIR = PROJECT_ROOT / "src"
        # GOOGLE_API_KEY="..." 또는 GOOGLE_API_KEY = '...' 형태 탐지
        hardcoded_pattern = re.compile(
            r"""GOOGLE_API_KEY\s*=\s*['"]([^'"]+)['"]"""
        )

        violations: list[str] = []
        for py_file in SRC_DIR.rglob("*.py"):
            if "__pycache__" in py_file.parts:
                continue
            source = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                match = hardcoded_pattern.search(line)
                if match:
                    value = match.group(1)
                    lower_value = value.lower()
                    # placeholder는 허용: '<', 'your', 'example', 'placeholder'
                    if any(tok in lower_value for tok in ("<", "your", "example", "placeholder")):
                        continue
                    violations.append(
                        f"{py_file.relative_to(PROJECT_ROOT)}:{lineno}: "
                        f"GOOGLE_API_KEY에 하드코딩된 값이 있습니다"
                    )

        assert not violations, (
            "src/ 아래 파일에 GOOGLE_API_KEY 하드코딩이 발견됐습니다 (요구사항 15.4, 15.5):\n"
            + "\n".join(f"  • {v}" for v in violations)
        )

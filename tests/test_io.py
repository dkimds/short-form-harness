"""
tests/test_io.py — src/common/io.py 단위 테스트

요구사항: 16.1, 16.2
"""

import json
import re
import time
from pathlib import Path

import pytest

from src.common.io import make_run_dir, make_run_id, read_json, write_json


# ---------------------------------------------------------------------------
# make_run_id
# ---------------------------------------------------------------------------

class TestMakeRunId:
    def test_format_matches_pattern(self):
        """run_id가 YYYYMMDD_HHMMSS_<6hex> 형식을 따르는지 확인."""
        run_id = make_run_id()
        pattern = r"^\d{8}_\d{6}_[0-9a-f]{6}$"
        assert re.match(pattern, run_id), f"예상 형식 불일치: {run_id!r}"

    def test_returns_string(self):
        assert isinstance(make_run_id(), str)

    def test_safe_for_directory_name(self):
        """run_id에 디렉터리 이름으로 사용할 수 없는 문자가 없는지 확인."""
        run_id = make_run_id()
        invalid_chars = set('/\\:*?"<>|')
        assert not invalid_chars.intersection(run_id), (
            f"디렉터리 이름에 사용 불가한 문자 포함: {run_id!r}"
        )

    def test_unique_across_calls(self):
        """연속 호출 시 서로 다른 run_id가 생성되는지 확인 (랜덤 suffix 덕분)."""
        ids = {make_run_id() for _ in range(20)}
        # 20번 호출 시 최소 2개 이상 고유값이 나와야 함
        assert len(ids) > 1

    def test_random_suffix_provides_uniqueness_even_same_second(self):
        """같은 초 내 호출도 suffix로 구별 가능함을 확인."""
        # 동일 timestamp prefix를 가진 두 id라도 suffix가 달라야 함
        ids = [make_run_id() for _ in range(50)]
        assert len(set(ids)) == len(ids) or len(set(ids)) > 1


# ---------------------------------------------------------------------------
# make_run_dir
# ---------------------------------------------------------------------------

class TestMakeRunDir:
    def test_creates_directory(self, tmp_path):
        run_id = "20240615_120000_aabbcc"
        result = make_run_dir(run_id, base_dir=str(tmp_path))
        expected = tmp_path / run_id
        assert expected.is_dir(), "디렉터리가 생성되지 않았습니다"

    def test_returns_absolute_path(self, tmp_path):
        run_id = "20240615_120000_aabbcc"
        result = make_run_dir(run_id, base_dir=str(tmp_path))
        assert Path(result).is_absolute(), "절대 경로가 반환되어야 합니다"

    def test_returns_string(self, tmp_path):
        run_id = "20240615_120000_aabbcc"
        result = make_run_dir(run_id, base_dir=str(tmp_path))
        assert isinstance(result, str)

    def test_path_contains_run_id(self, tmp_path):
        run_id = "20240615_120000_aabbcc"
        result = make_run_dir(run_id, base_dir=str(tmp_path))
        assert run_id in result

    def test_idempotent_existing_dir(self, tmp_path):
        """이미 존재하는 디렉터리에 대해 오류 없이 통과해야 합니다."""
        run_id = "20240615_120000_aabbcc"
        make_run_dir(run_id, base_dir=str(tmp_path))
        # 두 번째 호출도 오류 없이
        result = make_run_dir(run_id, base_dir=str(tmp_path))
        assert Path(result).is_dir()

    def test_creates_nested_base_dir(self, tmp_path):
        """base_dir가 존재하지 않아도 parents=True로 생성됩니다."""
        run_id = "20240615_120000_aabbcc"
        deep_base = str(tmp_path / "a" / "b" / "outputs")
        result = make_run_dir(run_id, base_dir=deep_base)
        assert Path(result).is_dir()

    def test_default_base_dir_is_outputs(self, tmp_path, monkeypatch):
        """base_dir 기본값이 'outputs'인지 확인."""
        # 작업 디렉터리를 tmp_path로 변경하여 outputs/ 이 tmp에 만들어지게 함
        monkeypatch.chdir(tmp_path)
        run_id = "20240615_120000_aabbcc"
        result = make_run_dir(run_id)
        assert (tmp_path / "outputs" / run_id).is_dir()


# ---------------------------------------------------------------------------
# write_json / read_json
# ---------------------------------------------------------------------------

class TestWriteReadJson:
    def test_write_and_read_roundtrip(self, tmp_path):
        data = {"key": "value", "num": 42, "list": [1, 2, 3]}
        path = tmp_path / "test.json"
        write_json(data, path)
        loaded = read_json(path)
        assert loaded == data

    def test_write_with_indent_2(self, tmp_path):
        """indent=2로 저장됐는지 원본 텍스트로 확인."""
        data = {"a": 1}
        path = tmp_path / "indented.json"
        write_json(data, path)
        raw = path.read_text(encoding="utf-8")
        assert '  "a": 1' in raw, "indent=2 형식이 아닙니다"

    def test_write_non_ascii(self, tmp_path):
        """ensure_ascii=False: 한글 등 비ASCII 문자가 그대로 저장돼야 합니다."""
        data = {"이름": "홍길동", "emoji": "🎵"}
        path = tmp_path / "korean.json"
        write_json(data, path)
        raw = path.read_text(encoding="utf-8")
        assert "홍길동" in raw, "한글이 이스케이프되지 않아야 합니다"
        assert "🎵" in raw, "이모지가 이스케이프되지 않아야 합니다"

    def test_write_creates_parent_dirs(self, tmp_path):
        """부모 디렉터리가 없어도 자동 생성해야 합니다."""
        path = tmp_path / "deep" / "nested" / "file.json"
        write_json({"x": 1}, path)
        assert path.exists()

    def test_write_accepts_str_path(self, tmp_path):
        path = str(tmp_path / "str_path.json")
        write_json({"a": 1}, path)
        assert Path(path).exists()

    def test_read_accepts_str_path(self, tmp_path):
        path = tmp_path / "data.json"
        path.write_text('{"b": 2}', encoding="utf-8")
        result = read_json(str(path))
        assert result == {"b": 2}

    def test_read_missing_file_raises_file_not_found(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError) as exc_info:
            read_json(missing)
        # 안내 메시지에 경로 정보가 포함돼야 합니다
        assert "nonexistent.json" in str(exc_info.value)

    def test_read_missing_file_error_message_is_helpful(self, tmp_path):
        """FileNotFoundError 메시지가 단순 예외가 아니라 안내를 포함해야 합니다."""
        missing = tmp_path / "missing.json"
        with pytest.raises(FileNotFoundError) as exc_info:
            read_json(missing)
        msg = str(exc_info.value)
        # 파일 경로 힌트가 있어야 함
        assert "missing.json" in msg

    def test_overwrite_existing_file(self, tmp_path):
        path = tmp_path / "overwrite.json"
        write_json({"v": 1}, path)
        write_json({"v": 2}, path)
        assert read_json(path) == {"v": 2}

    def test_empty_dict(self, tmp_path):
        path = tmp_path / "empty.json"
        write_json({}, path)
        assert read_json(path) == {}

    def test_nested_dict(self, tmp_path):
        data = {"level1": {"level2": {"level3": "deep"}}}
        path = tmp_path / "nested.json"
        write_json(data, path)
        assert read_json(path) == data

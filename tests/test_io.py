"""Tests for ts4k.state._io — atomic JSON write helper."""

from __future__ import annotations

import json
import os

import pytest

from ts4k.state._io import safe_write_json


class TestSafeWriteJson:
    def test_creates_file(self, tmp_path):
        p = tmp_path / "out.json"
        safe_write_json(p, {"a": 1})
        assert p.exists()
        assert json.loads(p.read_text("utf-8")) == {"a": 1}

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "sub" / "deep" / "out.json"
        safe_write_json(p, [1, 2, 3])
        assert json.loads(p.read_text("utf-8")) == [1, 2, 3]

    def test_overwrites_existing(self, tmp_path):
        p = tmp_path / "out.json"
        safe_write_json(p, {"v": 1})
        safe_write_json(p, {"v": 2})
        assert json.loads(p.read_text("utf-8")) == {"v": 2}

    def test_trailing_newline(self, tmp_path):
        p = tmp_path / "out.json"
        safe_write_json(p, {"x": 1})
        assert p.read_text("utf-8").endswith("\n")

    def test_no_temp_files_on_success(self, tmp_path):
        p = tmp_path / "out.json"
        safe_write_json(p, {"ok": True})
        leftover = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert leftover == []

    def test_no_temp_files_on_failure(self, tmp_path, monkeypatch):
        p = tmp_path / "out.json"
        # Make os.replace raise to simulate a failure after write
        def bad_replace(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", bad_replace)

        with pytest.raises(OSError, match="disk full"):
            safe_write_json(p, {"fail": True})

        leftover = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert leftover == []
        assert not p.exists()

    def test_sort_keys(self, tmp_path):
        p = tmp_path / "out.json"
        safe_write_json(p, {"z": 1, "a": 2}, sort_keys=True)
        text = p.read_text("utf-8")
        assert text.index('"a"') < text.index('"z"')

    def test_indent_none_compact(self, tmp_path):
        p = tmp_path / "out.json"
        safe_write_json(p, {"a": 1, "b": 2}, indent=None)
        text = p.read_text("utf-8")
        # Compact: no newlines inside the JSON (just trailing)
        assert text.strip().count("\n") == 0

    def test_indent_custom(self, tmp_path):
        p = tmp_path / "out.json"
        safe_write_json(p, {"a": 1}, indent=4)
        text = p.read_text("utf-8")
        assert "    " in text  # 4-space indent

    def test_default_indent_is_2(self, tmp_path):
        p = tmp_path / "out.json"
        safe_write_json(p, {"a": 1})
        text = p.read_text("utf-8")
        assert '  "a"' in text

    def test_non_serializable_raises(self, tmp_path):
        p = tmp_path / "out.json"
        with pytest.raises(TypeError):
            safe_write_json(p, {"bad": object()})
        # No temp files left
        leftover = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert leftover == []

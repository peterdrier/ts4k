"""Tests for ts4k.state.watermarks."""

import json
import os

import pytest

from ts4k.state import watermarks


@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    """Point watermarks at a temp directory for every test."""
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
    # Force module to re-evaluate the path
    monkeypatch.setattr(watermarks, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(watermarks, "_WM_FILE", tmp_path / "watermarks.json")
    return tmp_path


class TestGet:
    def test_returns_none_when_no_file(self):
        assert watermarks.get("g") is None

    def test_returns_none_for_unknown_source(self, tmp_config_dir):
        (tmp_config_dir / "watermarks.json").write_text('{"g": "2026-01-01T00:00:00Z"}')
        assert watermarks.get("w") is None

    def test_returns_stored_value(self, tmp_config_dir):
        (tmp_config_dir / "watermarks.json").write_text('{"g": "2026-01-01T00:00:00Z"}')
        assert watermarks.get("g") == "2026-01-01T00:00:00Z"


class TestUpdate:
    def test_creates_file_and_sets_value(self, tmp_config_dir):
        ts = watermarks.update("g", "2026-02-21T12:00:00Z")
        assert ts == "2026-02-21T12:00:00Z"

        data = json.loads((tmp_config_dir / "watermarks.json").read_text())
        assert data["g"] == "2026-02-21T12:00:00Z"

    def test_defaults_to_now(self):
        ts = watermarks.update("g")
        assert ts.startswith("202")
        assert ts.endswith("Z")
        assert watermarks.get("g") == ts

    def test_preserves_other_sources(self, tmp_config_dir):
        watermarks.update("g", "2026-01-01T00:00:00Z")
        watermarks.update("w", "2026-02-01T00:00:00Z")
        assert watermarks.get("g") == "2026-01-01T00:00:00Z"
        assert watermarks.get("w") == "2026-02-01T00:00:00Z"

    def test_overwrites_existing(self):
        watermarks.update("g", "2026-01-01T00:00:00Z")
        watermarks.update("g", "2026-06-15T00:00:00Z")
        assert watermarks.get("g") == "2026-06-15T00:00:00Z"


class TestClear:
    def test_clear_single(self):
        watermarks.update("g", "2026-01-01T00:00:00Z")
        watermarks.update("w", "2026-02-01T00:00:00Z")
        watermarks.clear("g")
        assert watermarks.get("g") is None
        assert watermarks.get("w") == "2026-02-01T00:00:00Z"

    def test_clear_all(self):
        watermarks.update("g", "2026-01-01T00:00:00Z")
        watermarks.update("w", "2026-02-01T00:00:00Z")
        watermarks.clear()
        assert watermarks.all() == {}

    def test_clear_nonexistent_is_safe(self):
        watermarks.clear("x")  # no error


class TestAll:
    def test_empty_when_no_file(self):
        assert watermarks.all() == {}

    def test_returns_all_watermarks(self):
        watermarks.update("g", "2026-01-01T00:00:00Z")
        watermarks.update("w", "2026-02-01T00:00:00Z")
        result = watermarks.all()
        assert result == {
            "g": "2026-01-01T00:00:00Z",
            "w": "2026-02-01T00:00:00Z",
        }


class TestCorruptFile:
    def test_handles_invalid_json(self, tmp_config_dir):
        (tmp_config_dir / "watermarks.json").write_text("not json")
        assert watermarks.get("g") is None
        # Should be able to overwrite corrupt file
        watermarks.update("g", "2026-01-01T00:00:00Z")
        assert watermarks.get("g") == "2026-01-01T00:00:00Z"

"""Tests for ts4k.state.keyed_watermarks."""

import json

import pytest

from ts4k.state import keyed_watermarks as kwm


@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    """Point keyed watermarks at a temp directory."""
    monkeypatch.setattr(kwm, "_CONFIG_DIR", tmp_path)
    return tmp_path


class TestGet:
    def test_returns_empty_when_no_file(self):
        assert kwm.get_all("life") == {}

    def test_returns_stored_values(self, tmp_config_dir):
        wm_dir = tmp_config_dir / "watermarks"
        wm_dir.mkdir()
        (wm_dir / "life.json").write_text('{"g": "2026-03-01T00:00:00Z", "o": "2026-03-02T00:00:00Z"}')
        result = kwm.get_all("life")
        assert result == {"g": "2026-03-01T00:00:00Z", "o": "2026-03-02T00:00:00Z"}

    def test_get_single_source(self, tmp_config_dir):
        wm_dir = tmp_config_dir / "watermarks"
        wm_dir.mkdir()
        (wm_dir / "life.json").write_text('{"g": "2026-03-01T00:00:00Z"}')
        assert kwm.get("life", "g") == "2026-03-01T00:00:00Z"
        assert kwm.get("life", "w") is None


class TestUpdate:
    def test_creates_dir_and_file(self, tmp_config_dir):
        kwm.update("life", {"g": "2026-03-08T12:00:00Z"})
        data = json.loads((tmp_config_dir / "watermarks" / "life.json").read_text())
        assert data == {"g": "2026-03-08T12:00:00Z"}

    def test_merges_with_existing(self, tmp_config_dir):
        kwm.update("life", {"g": "2026-03-01T00:00:00Z"})
        kwm.update("life", {"o": "2026-03-02T00:00:00Z"})
        result = kwm.get_all("life")
        assert result == {"g": "2026-03-01T00:00:00Z", "o": "2026-03-02T00:00:00Z"}

    def test_overwrites_existing_source(self, tmp_config_dir):
        kwm.update("life", {"g": "2026-03-01T00:00:00Z"})
        kwm.update("life", {"g": "2026-03-08T00:00:00Z"})
        assert kwm.get("life", "g") == "2026-03-08T00:00:00Z"

    def test_different_keys_independent(self, tmp_config_dir):
        kwm.update("life", {"g": "2026-03-01T00:00:00Z"})
        kwm.update("peter", {"g": "2026-03-05T00:00:00Z"})
        assert kwm.get("life", "g") == "2026-03-01T00:00:00Z"
        assert kwm.get("peter", "g") == "2026-03-05T00:00:00Z"


class TestListKeys:
    def test_empty_when_no_dir(self):
        assert kwm.list_keys() == []

    def test_returns_key_names(self, tmp_config_dir):
        wm_dir = tmp_config_dir / "watermarks"
        wm_dir.mkdir()
        (wm_dir / "life.json").write_text('{"g": "2026-03-01T00:00:00Z"}')
        (wm_dir / "peter.json").write_text('{"g": "2026-03-05T00:00:00Z"}')
        assert sorted(kwm.list_keys()) == ["life", "peter"]


class TestCorruptFile:
    def test_handles_invalid_json(self, tmp_config_dir):
        wm_dir = tmp_config_dir / "watermarks"
        wm_dir.mkdir()
        (wm_dir / "life.json").write_text("not json")
        assert kwm.get_all("life") == {}
        kwm.update("life", {"g": "2026-03-01T00:00:00Z"})
        assert kwm.get("life", "g") == "2026-03-01T00:00:00Z"

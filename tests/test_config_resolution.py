"""Tests for centralized config directory resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from ts4k.state import (
    get_config_dir,
    reset,
    resolve_config_dir,
    set_config_dir,
)


@pytest.fixture(autouse=True)
def _clean_state():
    """Ensure each test starts with a clean resolver cache."""
    reset()
    yield
    reset()


class TestResolveConfigDir:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        """TS4K_CONFIG_DIR env var takes priority over everything."""
        env_dir = tmp_path / "from-env"
        env_dir.mkdir()
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(env_dir))

        # Also create .ts4k/ in cwd to prove env wins
        local_dir = tmp_path / "project" / ".ts4k"
        local_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path / "project")

        result = resolve_config_dir()
        assert result.path == env_dir
        assert result.reason == "env"

    def test_local_dir_over_default(self, tmp_path, monkeypatch):
        """.ts4k/ in cwd wins over global default."""
        monkeypatch.delenv("TS4K_CONFIG_DIR", raising=False)
        local_dir = tmp_path / ".ts4k"
        local_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = resolve_config_dir()
        assert result.path == local_dir
        assert result.reason == "local"

    def test_default_when_no_env_no_local(self, tmp_path, monkeypatch):
        """Falls back to ~/.config/ts4k/ when no env var and no .ts4k/."""
        monkeypatch.delenv("TS4K_CONFIG_DIR", raising=False)
        monkeypatch.chdir(tmp_path)  # no .ts4k/ here

        result = resolve_config_dir()
        assert result.path == Path.home() / ".config" / "ts4k"
        assert result.reason == "default"

    def test_env_var_beats_local_dir(self, tmp_path, monkeypatch):
        """Env var wins even when .ts4k/ exists in cwd."""
        env_dir = tmp_path / "env-cfg"
        env_dir.mkdir()
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(env_dir))

        local_dir = tmp_path / ".ts4k"
        local_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        result = resolve_config_dir()
        assert result.path == env_dir
        assert result.reason == "env"


class TestGetConfigDir:
    def test_caches_result(self, tmp_path, monkeypatch):
        """get_config_dir() resolves once and caches."""
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        first = get_config_dir()
        second = get_config_dir()
        assert first is second

    def test_reset_clears_cached(self, tmp_path, monkeypatch):
        """reset() allows re-resolution."""
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path / "a"))
        (tmp_path / "a").mkdir()
        first = get_config_dir()

        reset()
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path / "b"))
        (tmp_path / "b").mkdir()
        second = get_config_dir()

        assert first.path != second.path


class TestSetConfigDir:
    def test_set_config_dir_patches_all_modules(self, tmp_path):
        """set_config_dir should patch _CONFIG_DIR on all 7 state modules."""
        from ts4k.state import batch, cache, contacts, filters, sources, stats, watermarks

        set_config_dir(tmp_path, reason="test")

        assert watermarks._CONFIG_DIR == tmp_path
        assert watermarks._WM_FILE == tmp_path / "watermarks.json"

        assert sources._CONFIG_DIR == tmp_path
        assert sources._SOURCES_FILE == tmp_path / "sources.json"

        assert filters._CONFIG_DIR == tmp_path
        assert filters._FILTERS_FILE == tmp_path / "filters.json"

        assert stats._CONFIG_DIR == tmp_path
        assert stats._STATS_FILE == tmp_path / "stats.json"

        assert contacts._CONFIG_DIR == tmp_path
        assert contacts._CONTACTS_FILE == tmp_path / "contacts.json"

        assert batch._CONFIG_DIR == tmp_path
        assert batch._BATCH_FILE == tmp_path / "batch.json"

        assert cache._CONFIG_DIR == tmp_path
        assert cache._CACHE_DIR == tmp_path / "cache"
        assert cache._INDEX_FILE == tmp_path / "cache" / "index.json"

    def test_set_updates_cached(self, tmp_path):
        """set_config_dir updates the cached value."""
        set_config_dir(tmp_path, reason="test")
        result = get_config_dir()
        assert result.path == tmp_path
        assert result.reason == "test"

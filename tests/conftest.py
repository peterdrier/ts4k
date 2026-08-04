"""Shared test fixtures for ts4k."""

from __future__ import annotations


import pytest


@pytest.fixture
def ts4k_config(tmp_path, monkeypatch):
    """Provide an isolated ts4k config directory.

    Sets ``TS4K_CONFIG_DIR``, calls ``set_config_dir`` to propagate to all
    state modules, and resets on teardown.
    """
    from ts4k import state

    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
    state.set_config_dir(tmp_path, reason="test")
    yield tmp_path
    state.reset()

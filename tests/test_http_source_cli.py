"""CLI tests for `ts4k src add <prefix> http ...` (#31)."""

from __future__ import annotations

import argparse

from ts4k import cli, commands
from ts4k.adapters.http import HTTPAdapter
from ts4k.state import sources


def test_add_requires_url(ts4k_config, capsys):
    cli._cmd_sources(argparse.Namespace(
        action="add", prefix="h", provider="http", params=["header=X-Api-Key: abc"],
    ))
    out = capsys.readouterr().out
    assert "url is required" in out
    assert sources.get("h") is None


def test_add_stores_url(ts4k_config, capsys):
    cli._cmd_sources(argparse.Namespace(
        action="add", prefix="h", provider="http",
        params=["url=https://example.com/api/notifications"],
    ))
    entry = sources.get("h")
    assert entry["url"] == "https://example.com/api/notifications"


def test_repeated_header_flag_accumulates(ts4k_config):
    cli._cmd_sources(argparse.Namespace(
        action="add", prefix="h", provider="http",
        params=[
            "url=https://example.com/api/notifications",
            "header=X-Api-Key: abc123",
            "header=Authorization: Bearer xyz",
        ],
    ))
    entry = sources.get("h")
    assert entry["header"] == ["X-Api-Key: abc123", "Authorization: Bearer xyz"]


def test_single_header_flag_stays_a_list(ts4k_config):
    """Even a single occurrence is stored as a list — HTTPAdapterConfig.header
    accepts either shape, but a consistent shape keeps sources.json predictable."""
    cli._cmd_sources(argparse.Namespace(
        action="add", prefix="h", provider="http",
        params=["url=https://example.com/api/notifications", "header=X-Api-Key: abc123"],
    ))
    entry = sources.get("h")
    assert entry["header"] == ["X-Api-Key: abc123"]


def test_make_adapter_builds_http_adapter(ts4k_config):
    cli._cmd_sources(argparse.Namespace(
        action="add", prefix="h", provider="http",
        params=["url=https://example.com/api/notifications", "header=X-Api-Key: abc123"],
    ))
    cfg = sources.get("h")
    adapter = commands._make_adapter("h", cfg)
    assert isinstance(adapter, HTTPAdapter)
    assert adapter.source_prefix == "h"


def test_make_adapter_returns_none_without_url(ts4k_config):
    assert commands._make_adapter("h", {"provider": "http"}) is None


def test_add_redacts_header_in_output(ts4k_config, capsys):
    """#31 review F1 — `header` commonly carries an API key or bearer
    token; it must never be echoed in full on `src add`."""
    cli._cmd_sources(argparse.Namespace(
        action="add", prefix="h", provider="http",
        params=[
            "url=https://example.com/api/notifications",
            "header=Authorization: Bearer super-secret-token",
        ],
    ))
    out = capsys.readouterr().out
    assert "super-secret-token" not in out
    assert "<redacted>" in out
    # Storage is unchanged — only display is redacted.
    assert sources.get("h")["header"] == ["Authorization: Bearer super-secret-token"]


def test_list_redacts_header_in_output(ts4k_config, capsys):
    sources.add(
        "h", provider="http", url="https://example.com/api/notifications",
        header=["Authorization: Bearer super-secret-token"],
    )
    capsys.readouterr()  # discard `add`'s own output, if any
    cli._cmd_sources(argparse.Namespace(action="list"))
    out = capsys.readouterr().out
    assert "super-secret-token" not in out
    assert "<redacted>" in out

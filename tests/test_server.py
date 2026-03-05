"""Tests for the ts4k MCP server (server.py).

Verifies that all 11 tools are registered with correct names and parameter
schemas, and that context scoping patches the right module paths.
"""

from __future__ import annotations

import pytest

from ts4k.server import mcp, _apply_context


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "updates",
    "get",
    "thread",
    "list",
    "status",
    "contacts",
    "cache",
    "filter",
    "preload",
    "preload_status",
    "overview",
}


class TestToolRegistration:
    def _get_tool_names(self) -> set[str]:
        """Extract registered tool names from the FastMCP instance."""
        # FastMCP stores tools in _tool_manager._tools dict
        manager = mcp._tool_manager
        return set(manager._tools.keys())

    def test_all_tools_registered(self):
        """All 11 expected tools are registered."""
        names = self._get_tool_names()
        assert EXPECTED_TOOLS == names, f"Missing: {EXPECTED_TOOLS - names}, Extra: {names - EXPECTED_TOOLS}"

    def test_exactly_eleven_tools(self):
        """No extra tools registered."""
        assert len(self._get_tool_names()) == 11

    def test_updates_params(self):
        """updates has expected parameters."""
        tool = mcp._tool_manager._tools["updates"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "source" in props
        assert "since" in props
        assert "count" in props
        assert "fmt" in props
        assert "filter" in props

    def test_get_params(self):
        """get requires id."""
        tool = mcp._tool_manager._tools["get"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "id" in props

    def test_thread_params(self):
        """thread requires tid."""
        tool = mcp._tool_manager._tools["thread"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "tid" in props

    def test_list_params(self):
        """list has source, query, count params."""
        tool = mcp._tool_manager._tools["list"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "source" in props
        assert "query" in props
        assert "count" in props

    def test_contacts_params(self):
        """contacts has action, alias, identifiers, term."""
        tool = mcp._tool_manager._tools["contacts"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "action" in props
        assert "alias" in props
        assert "identifiers" in props
        assert "term" in props

    def test_filter_params(self):
        """filter has action and value."""
        tool = mcp._tool_manager._tools["filter"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "action" in props
        assert "value" in props

    def test_preload_params(self):
        """preload has source, query, contact, background, etc."""
        tool = mcp._tool_manager._tools["preload"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "source" in props
        assert "query" in props
        assert "contact" in props
        assert "pages" in props
        assert "bodies" in props
        assert "resume" in props
        assert "background" in props

    def test_overview_params(self):
        """overview has source, contact, period, fmt, top."""
        tool = mcp._tool_manager._tools["overview"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "source" in props
        assert "contact" in props
        assert "period" in props
        assert "fmt" in props
        assert "top" in props

    def test_preload_status_no_params(self):
        """preload_status takes no parameters."""
        tool = mcp._tool_manager._tools["preload_status"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert len(props) == 0

    def test_status_has_optional_params(self):
        """status has optional live/source/fmt params (all defaulted)."""
        tool = mcp._tool_manager._tools["status"]
        schema = tool.parameters
        required = schema.get("required", [])
        assert len(required) == 0  # all optional
        props = schema.get("properties", {})
        assert "live" in props
        assert "source" in props
        assert "fmt" in props


# ---------------------------------------------------------------------------
# Context scoping
# ---------------------------------------------------------------------------


class TestContextScoping:
    def test_apply_context_patches_watermarks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k.state import watermarks as wm

        _apply_context("test-ctx")
        assert "contexts" in str(wm._CONFIG_DIR)
        assert "test-ctx" in str(wm._CONFIG_DIR)
        assert wm._WM_FILE.name == "watermarks.json"

    def test_apply_context_patches_stats(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k.state import stats as st

        _apply_context("test-ctx")
        assert "contexts" in str(st._CONFIG_DIR)
        assert "test-ctx" in str(st._CONFIG_DIR)
        assert st._STATS_FILE.name == "stats.json"

    def test_context_does_not_affect_contacts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k.state import contacts as c

        original_dir = c._CONFIG_DIR
        _apply_context("test-ctx")
        # Contacts should NOT be patched
        assert c._CONFIG_DIR == original_dir

"""Tests for the ts4k MCP server (server.py).

Verifies that all 8 tools are registered with correct names and parameter
schemas, and that context scoping patches the right module paths.
"""

from __future__ import annotations

import pytest

from ts4k.server import mcp, _apply_context


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "ts4k_whatsnew",
    "ts4k_get",
    "ts4k_thread",
    "ts4k_list",
    "ts4k_status",
    "ts4k_contacts",
    "ts4k_cache",
    "ts4k_filter",
}


class TestToolRegistration:
    def _get_tool_names(self) -> set[str]:
        """Extract registered tool names from the FastMCP instance."""
        # FastMCP stores tools in _tool_manager._tools dict
        manager = mcp._tool_manager
        return set(manager._tools.keys())

    def test_all_tools_registered(self):
        """All 8 expected tools are registered."""
        names = self._get_tool_names()
        assert EXPECTED_TOOLS == names, f"Missing: {EXPECTED_TOOLS - names}, Extra: {names - EXPECTED_TOOLS}"

    def test_exactly_eight_tools(self):
        """No extra tools registered."""
        assert len(self._get_tool_names()) == 8

    def test_whatsnew_params(self):
        """ts4k_whatsnew has expected parameters."""
        tool = mcp._tool_manager._tools["ts4k_whatsnew"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "source" in props
        assert "since" in props
        assert "count" in props
        assert "fmt" in props
        assert "apply_filter" in props

    def test_get_params(self):
        """ts4k_get requires msg_id."""
        tool = mcp._tool_manager._tools["ts4k_get"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "msg_id" in props

    def test_thread_params(self):
        """ts4k_thread requires thread_id."""
        tool = mcp._tool_manager._tools["ts4k_thread"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "thread_id" in props

    def test_list_params(self):
        """ts4k_list has source, query, count params."""
        tool = mcp._tool_manager._tools["ts4k_list"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "source" in props
        assert "query" in props
        assert "count" in props

    def test_contacts_params(self):
        """ts4k_contacts has action, alias, identifiers, term."""
        tool = mcp._tool_manager._tools["ts4k_contacts"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "action" in props
        assert "alias" in props
        assert "identifiers" in props
        assert "term" in props

    def test_filter_params(self):
        """ts4k_filter has action and value."""
        tool = mcp._tool_manager._tools["ts4k_filter"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "action" in props
        assert "value" in props

    def test_status_no_required_params(self):
        """ts4k_status takes no parameters."""
        tool = mcp._tool_manager._tools["ts4k_status"]
        schema = tool.parameters
        props = schema.get("properties", {})
        # status has no user-facing params
        assert len(props) == 0


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

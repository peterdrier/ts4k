"""Tests for the ts4k MCP server (server.py).

Verifies that all 7 tools are registered with correct names and parameter
schemas, that the admin tool routes commands through the CLI parser,
and that context scoping patches the right module paths.
"""

from __future__ import annotations

import pytest

from ts4k.server import mcp, _apply_context


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "whatsnew",
    "get",
    "thread",
    "list",
    "status",
    "overview",
    "admin",
    "manage",
    "draft",
    "cal",
    "cal_create",
    "cal_manage",
}


class TestToolRegistration:
    def _get_tool_names(self) -> set[str]:
        """Extract registered tool names from the FastMCP instance."""
        # FastMCP stores tools in _tool_manager._tools dict
        manager = mcp._tool_manager
        return set(manager._tools.keys())

    def test_all_tools_registered(self):
        """All 7 expected tools are registered."""
        names = self._get_tool_names()
        assert EXPECTED_TOOLS == names, f"Missing: {EXPECTED_TOOLS - names}, Extra: {names - EXPECTED_TOOLS}"

    def test_exactly_twelve_tools(self):
        """No extra tools registered."""
        assert len(self._get_tool_names()) == 12

    def test_list_params(self):
        """list has all stackable filter parameters."""
        tool = mcp._tool_manager._tools["list"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "source" in props
        assert "since" in props
        assert "query" in props
        assert "sender" in props
        assert "domain" in props
        assert "count" in props
        assert "fmt" in props
        assert "filter" in props

    def test_whatsnew_params(self):
        """whatsnew has key (required) plus source, count, fmt, filter."""
        tool = mcp._tool_manager._tools["whatsnew"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "key" in props
        assert "source" in props
        assert "count" in props
        assert "fmt" in props
        assert "filter" in props
        required = schema.get("required", [])
        assert "key" in required

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

    def test_admin_has_cmd_param(self):
        """admin has a single required cmd parameter."""
        tool = mcp._tool_manager._tools["admin"]
        schema = tool.parameters
        props = schema.get("properties", {})
        assert "cmd" in props
        assert len(props) == 1
        required = schema.get("required", [])
        assert "cmd" in required

    def test_cal_tool_registered(self):
        """cal tool is registered with expected params."""
        names = self._get_tool_names()
        assert "cal" in names
        tool = mcp._tool_manager._tools["cal"]
        props = tool.parameters.get("properties", {})
        assert "view" in props
        assert "ref" in props
        assert "source" in props
        assert "count" in props
        assert "from_date" in props
        assert "to_date" in props
        assert "format" in props

    def test_cal_create_tool_registered(self):
        """cal_create tool is registered with expected params."""
        names = self._get_tool_names()
        assert "cal_create" in names
        tool = mcp._tool_manager._tools["cal_create"]
        props = tool.parameters.get("properties", {})
        assert "source" in props
        assert "title" in props
        assert "start" in props
        assert "end" in props
        assert "attendees" in props
        required = tool.parameters.get("required", [])
        assert "source" in required
        assert "title" in required

    def test_cal_manage_tool_registered(self):
        """cal_manage tool is registered with expected params."""
        names = self._get_tool_names()
        assert "cal_manage" in names
        tool = mcp._tool_manager._tools["cal_manage"]
        props = tool.parameters.get("properties", {})
        assert "action" in props
        assert "ref" in props
        assert "status" in props
        assert "title" in props
        required = tool.parameters.get("required", [])
        assert "action" in required
        assert "ref" in required


# ---------------------------------------------------------------------------
# whatsnew --threads forwarding
# ---------------------------------------------------------------------------


class TestWhatsnewThreadsForwarding:
    @pytest.mark.asyncio
    async def test_whatsnew_forwards_threads_true(self, monkeypatch):
        """The whatsnew tool wrapper must pass threads through to
        commands.whatsnew — it can't reach thread mode otherwise."""
        from ts4k import server
        from ts4k.commands import CommandResult

        captured = {}

        async def fake_whatsnew(**kwargs):
            captured.update(kwargs)
            return CommandResult(output="ok")

        monkeypatch.setattr(server.commands, "whatsnew", fake_whatsnew)

        await server.whatsnew(key="life", threads=True)

        assert captured.get("threads") is True

    @pytest.mark.asyncio
    async def test_whatsnew_threads_defaults_false(self, monkeypatch):
        from ts4k import server
        from ts4k.commands import CommandResult

        captured = {}

        async def fake_whatsnew(**kwargs):
            captured.update(kwargs)
            return CommandResult(output="ok")

        monkeypatch.setattr(server.commands, "whatsnew", fake_whatsnew)

        await server.whatsnew(key="life")

        assert captured.get("threads") is False

    def test_whatsnew_schema_has_threads_param(self):
        """CLI/MCP parity: --threads on the CLI implies a threads param here."""
        tool = mcp._tool_manager._tools["whatsnew"]
        props = tool.parameters.get("properties", {})
        assert "threads" in props


# ---------------------------------------------------------------------------
# Admin tool routing
# ---------------------------------------------------------------------------


class TestAdminRouting:
    @pytest.mark.asyncio
    async def test_admin_routes_contacts_list(self, tmp_path, monkeypatch):
        """admin('contacts list') returns contact list output."""
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k import state
        state.set_config_dir(tmp_path, reason="test")

        from ts4k.server import _run_cli_command
        result = await _run_cli_command("contacts list")
        # Should succeed (empty list is fine, no error)
        assert "Error" not in result or "error" not in result.lower()

    @pytest.mark.asyncio
    async def test_admin_routes_cache_stats(self, tmp_path, monkeypatch):
        """admin('cache stats') returns cache stats."""
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k import state
        state.set_config_dir(tmp_path, reason="test")

        from ts4k.server import _run_cli_command
        result = await _run_cli_command("cache stats")
        assert "Error" not in result

    @pytest.mark.asyncio
    async def test_admin_routes_filter_show(self, tmp_path, monkeypatch):
        """admin('filter show') returns filter config."""
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k import state
        state.set_config_dir(tmp_path, reason="test")

        from ts4k.server import _run_cli_command
        result = await _run_cli_command("filter show")
        assert "Error" not in result

    @pytest.mark.asyncio
    async def test_admin_rejects_non_admin_command(self):
        """admin rejects commands not in the allow list."""
        from ts4k.server import _run_cli_command
        result = await _run_cli_command("updates --since 2d")
        assert "not an admin command" in result

    @pytest.mark.asyncio
    async def test_admin_empty_command(self):
        """admin rejects empty input."""
        from ts4k.server import _run_cli_command
        result = await _run_cli_command("")
        assert "empty command" in result


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

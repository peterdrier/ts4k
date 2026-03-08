"""ts4k MCP server — expose ts4k as MCP tools for LLM agents.

Usage::

    ts4k-mcp                           # stdio transport (default)
    ts4k-mcp --transport http          # HTTP transport on port 8000
    ts4k-mcp --transport http --port 9000
    ts4k-mcp --context agent-morning   # scoped watermarks + stats

The ``--context`` flag scopes watermarks and stats to a subdirectory so
multiple agents/sessions can have independent read positions while sharing
the same source config, contacts, and filters.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import os
import shlex
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ts4k import commands
from ts4k.state.refs import RefTable

mcp = FastMCP(
    name="ts4k",
    instructions=(
        "ts4k (Token Saver 4000) provides token-efficient access to messages "
        "across Gmail, WhatsApp, O365, and other platforms. Use pipe format "
        "(default) for maximum token efficiency. Listings use short refs "
        "(#1, #2, ...) — use these with get/thread instead of full IDs."
    ),
)

# Session-scoped ref table — accumulates across tool calls within one connection.
_refs = RefTable()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def updates(
    source: str = "all",
    since: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    filter: bool = False,
) -> str:
    """Fetch messages by time range. Stateless — no watermarks.

    Listings use short refs (#1, #2, ...) — pass these to get/thread.

    Args:
        source: Source prefix (e.g. "g"), provider name ("gmail"), or "all".
        since: Time range — "2d", "7d", ISO timestamp. Defaults to 1d if omitted.
        count: Maximum messages to return (default 20).
        fmt: Output format — "pipe" (default, most compact), "json", or "xml".
        filter: Apply configured skip filters (default off).
    """
    result = await commands.updates(
        source=source,
        since=since,
        count=count,
        fmt=fmt,
        filter=filter,
        ref_table=_refs,
    )
    if result.error:
        return result.error
    return result.output


@mcp.tool()
async def get(id: str, fmt: str = "pipe") -> str:
    """Read a single message by its prefixed ID or short ref.

    Args:
        id: Message ID (e.g. "g:18f6a2b3c4e5f6a7") or short ref (e.g. "#3").
        fmt: Output format — "pipe" (default), "json", or "xml".
    """
    result = await commands.get_message(id=id, fmt=fmt, ref_table=_refs)
    if result.error:
        return result.error
    return result.output


@mcp.tool()
async def thread(tid: str, fmt: str = "pipe") -> str:
    """Read a thread or conversation by its prefixed ID or short ref.

    Args:
        tid: Thread/chat ID (e.g. "g:18f6a2b3c4e5f6a8") or short ref (e.g. "#3").
        fmt: Output format — "pipe" (default), "json", or "xml".
    """
    result = await commands.get_thread(tid=tid, fmt=fmt, ref_table=_refs)
    if result.error:
        return result.error
    return result.output


@mcp.tool(name="list")
async def list_tool(
    source: str = "all",
    query: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    filter: bool = False,
) -> str:
    """Search and list messages matching a query.

    Listings use short refs (#1, #2, ...) — pass these to get/thread.

    Args:
        source: Source prefix, provider name, or "all".
        query: Search query string (provider-specific).
        count: Maximum messages to return (default 20).
        fmt: Output format — "pipe" (default), "json", or "xml".
        filter: Apply configured skip filters (default off).
    """
    result = await commands.list_messages(
        source=source,
        query=query,
        count=count,
        fmt=fmt,
        filter=filter,
        ref_table=_refs,
    )
    if result.error:
        return result.error
    return result.output


@mcp.tool()
async def status(
    live: bool = False,
    source: str | None = None,
    fmt: str = "pipe",
) -> str:
    """Show operational status: sources, watermarks, contacts, filters, stats.

    Args:
        live: Include live mailbox label/folder counts (default off).
        source: Limit live stats to this source prefix (e.g. "g").
        fmt: Format for mailbox section — "pipe" (default), "json", or "xml".
    """
    mbox = None
    if live:
        mbox = await commands.get_mailbox_stats(source=source)
    return commands.get_status(mailbox_stats_data=mbox, fmt=fmt)


@mcp.tool()
async def admin(cmd: str) -> str:
    """Run an admin/setup command. Pass CLI syntax as a single string.

    Commands:
      contacts list|link|unlink|find    Cross-platform identity map
      filter show|add-sender|rm-sender|add-domain|rm-domain|add-pattern|rm-pattern|reset
      cache stats|clear [--source S]    Manage message cache
      preload -s SOURCE [--query Q] [--since 30d] [--bg]
      preload --status|--cancel JOB_ID

    Examples:
      "contacts link sarah g:sarah@gmail.com w:447@wa"
      "filter add-sender spammer@example.com"
      "cache stats"
      "preload -s g --since 30d"
    """
    return await _run_cli_command(cmd)


@mcp.tool()
def overview(
    source: str | None = None,
    contact: str | None = None,
    period: str | None = None,
    fmt: str = "pipe",
    top: int = 10,
) -> str:
    """Hierarchical overview of cached messages. Cache-only, instant.

    Three view levels:
    - Top-level (no args): sources summary with message counts and top senders.
    - Source drill-down (source="g"): top senders and top threads for one source.
    - Contact drill-down (contact="alice"): cross-source breakdown with quarterly periods.

    Args:
        source: Source prefix to drill into (e.g. "g", "o").
        contact: Contact alias or name to drill into.
        period: Filter by period — "2025", "2025-Q1", "2025-03", or "2025-01..2025-06".
        fmt: Output format — "pipe" (default), "json", or "xml".
        top: Number of top senders/threads to show (default 10).
    """
    return commands.overview(
        source=source, contact=contact, period=period, fmt=fmt, top=top
    )


# ---------------------------------------------------------------------------
# Admin CLI router
# ---------------------------------------------------------------------------

# Commands allowed through the admin tool
_ADMIN_COMMANDS = {"contacts", "c", "filter", "f", "cache", "preload"}
_admin_lock = asyncio.Lock()


async def _run_cli_command(cmd: str) -> str:
    """Route a CLI command string through the ts4k parser.

    Parses *cmd* with the CLI parser, captures stdout/stderr, and returns
    the output.  Handles both sync and async command handlers.
    """
    from ts4k.cli import _build_parser

    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return f"Error: bad quoting in command: {exc}"

    if not argv:
        return "Error: empty command. Try: contacts list, cache stats, filter show"

    if argv[0] not in _ADMIN_COMMANDS:
        return (
            f"Error: '{argv[0]}' is not an admin command. "
            "Available: contacts, filter, cache, preload"
        )

    parser = _build_parser()
    stdout = io.StringIO()
    stderr = io.StringIO()

    # Lock protects the process-global redirect_stdout/redirect_stderr so
    # concurrent MCP requests (possible on HTTP transport) don't interleave.
    async with _admin_lock:
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                args = parser.parse_args(argv)

                if not hasattr(args, "func") or args.func is None:
                    return f"Error: incomplete command. Try: {argv[0]} --help"

                if asyncio.iscoroutinefunction(args.func):
                    await args.func(args)
                else:
                    args.func(args)

        except SystemExit:
            # argparse or handler called sys.exit — return whatever was printed
            err = stderr.getvalue().strip()
            out = stdout.getvalue().strip()
            return err or out or f"Error: command failed: {cmd}"

    return stdout.getvalue().strip() or stderr.getvalue().strip()


# ---------------------------------------------------------------------------
# Context scoping
# ---------------------------------------------------------------------------


def _apply_context(context: str) -> None:
    """Scope watermarks and stats to a context subdirectory.

    Sources, contacts, and filters remain in the global config dir.
    """
    from ts4k import state
    from ts4k.state import stats as stats_mod
    from ts4k.state import watermarks as wm_mod

    base = state.get_config_dir().path
    ctx_dir = base / "contexts" / context

    # Patch the module-level config dir and derived file paths
    wm_mod._CONFIG_DIR = ctx_dir
    wm_mod._WM_FILE = ctx_dir / "watermarks.json"

    stats_mod._CONFIG_DIR = ctx_dir
    stats_mod._STATS_FILE = ctx_dir / "stats.json"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """MCP server entry point.  Called by the ``ts4k-mcp`` console script."""
    parser = argparse.ArgumentParser(
        prog="ts4k-mcp",
        description="ts4k MCP server — token-efficient messaging tools for LLM agents.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument(
        "--context",
        help="Scope watermarks/stats to a named context (e.g. agent-morning)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP port (only with --transport http, default: 8000)",
    )

    args = parser.parse_args()

    # Resolve and propagate config dir to all state modules
    from ts4k import state

    resolved = state.get_config_dir()
    state.set_config_dir(resolved.path, reason=resolved.reason)

    if args.context:
        _apply_context(args.context)

    if args.transport == "http":
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

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
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ts4k import commands

mcp = FastMCP(
    name="ts4k",
    instructions=(
        "ts4k (Token Saver 4000) provides token-efficient access to messages "
        "across Gmail, WhatsApp, O365, and other platforms. Use pipe format "
        "(default) for maximum token efficiency. Message IDs are prefixed "
        "with the source (e.g. g:abc123, w:3EB05C, o:AAMk...)."
    ),
)


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
    """Fetch new messages since the last check (updates watermark).

    Args:
        source: Source prefix (e.g. "g"), provider name ("gmail"), or "all".
        since: Time range — "2d", "7d", ISO timestamp, or omit for watermark.
        count: Maximum messages to return (default 20).
        fmt: Output format — "pipe" (default, most compact), "json", or "xml".
        filter: Apply configured skip filters (default off).
    """
    result = await commands.whatsnew(
        source=source,
        since=since,
        count=count,
        fmt=fmt,
        filter=filter,
    )
    if result.error:
        return result.error
    return result.output


@mcp.tool()
async def get(id: str, fmt: str = "pipe") -> str:
    """Read a single message by its prefixed ID.

    Args:
        id: Message ID with source prefix (e.g. "g:18f6a2b3c4e5f6a7").
        fmt: Output format — "pipe" (default), "json", or "xml".
    """
    result = await commands.get_message(id=id, fmt=fmt)
    if result.error:
        return result.error
    return result.output


@mcp.tool()
async def thread(tid: str, fmt: str = "pipe") -> str:
    """Read a thread or conversation by its prefixed ID.

    Args:
        tid: Thread/chat ID with source prefix (e.g. "g:18f6a2b3c4e5f6a8").
        fmt: Output format — "pipe" (default), "json", or "xml".
    """
    result = await commands.get_thread(tid=tid, fmt=fmt)
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
    )
    if result.error:
        return result.error
    return result.output


@mcp.tool()
def status() -> str:
    """Show operational status: sources, watermarks, contacts, filters, stats."""
    return commands.get_status()


@mcp.tool()
def contacts(
    action: str = "list",
    alias: str | None = None,
    identifiers: list[str] | None = None,
    term: str | None = None,
) -> str:
    """Manage the cross-platform contact identity map.

    Args:
        action: One of "link", "unlink", "find", "list".
        alias: Contact alias (required for link/unlink).
        identifiers: Platform IDs to link/unlink (e.g. ["g:sarah@gmail.com"]).
        term: Search term (required for find).
    """
    return commands.manage_contacts(
        action=action, alias=alias, identifiers=identifiers, term=term
    )


@mcp.tool()
def cache(action: str = "stats", source: str | None = None, stale: bool = False) -> str:
    """Manage the message cache.

    Args:
        action: "stats" (show cache info) or "clear" (purge cached messages).
        source: For clear, limit to this source prefix (e.g. "g", "o"). Default: all.
        stale: For clear, only remove entries from an older schema version.
    """
    return commands.manage_cache(action=action, source=source, stale=stale)


@mcp.tool()
async def preload(
    source: str,
    query: str | None = None,
    contact: str | None = None,
    since: str | None = None,
    pages: int = 10,
    bodies: bool = False,
    resume: str | None = None,
    background: bool = False,
) -> str:
    """Preload messages into cache by paginating through history.

    Args:
        source: Source prefix (e.g. "g") or provider name ("gmail").
        query: Search query (provider-specific). Optional.
        contact: Contact alias — auto-expands to bidirectional query.
        since: Start date: ISO timestamp or "Nd" shorthand (e.g. "30d").
        pages: Maximum pages to fetch (default 10, lower for MCP).
        bodies: Also fetch full message bodies (slower).
        resume: Job ID to resume an interrupted preload.
        background: Run in background (returns job ID immediately).
    """
    if background:
        return commands.spawn_background_preload(
            source=source,
            query=query,
            contact=contact,
            since=since,
            pages=pages,
            bodies=bodies,
        )
    return await commands.preload(
        source=source,
        query=query,
        contact=contact,
        since=since,
        pages=pages,
        bodies=bodies,
        resume=resume,
    )


@mcp.tool()
def preload_status() -> str:
    """Show status of all preload jobs."""
    return commands.manage_preload("status")


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


@mcp.tool(name="filter")
def filter_tool(action: str = "show", value: str | None = None) -> str:
    """Manage message skip filters.

    Args:
        action: One of "show", "add-sender", "rm-sender", "add-domain",
                "rm-domain", "add-pattern", "rm-pattern", "skip-groups", "reset".
        value: Value for add/rm/set actions.
    """
    return commands.manage_filters(action=action, value=value)


# ---------------------------------------------------------------------------
# Context scoping
# ---------------------------------------------------------------------------


def _apply_context(context: str) -> None:
    """Scope watermarks and stats to a context subdirectory.

    Sources, contacts, and filters remain in the global config dir.
    """
    from ts4k.state import stats as stats_mod
    from ts4k.state import watermarks as wm_mod

    base = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
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

    if args.context:
        _apply_context(args.context)

    if args.transport == "http":
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

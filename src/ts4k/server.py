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
async def ts4k_whatsnew(
    source: str = "all",
    since: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    apply_filter: bool = False,
) -> str:
    """Fetch new messages since the last check (updates watermark).

    Args:
        source: Source prefix (e.g. "g"), provider name ("gmail"), or "all".
        since: Time range — "2d", "7d", ISO timestamp, or omit for watermark.
        count: Maximum messages to return (default 20).
        fmt: Output format — "pipe" (default, most compact), "json", or "xml".
        apply_filter: Apply configured skip filters (default off).
    """
    result = await commands.whatsnew(
        source=source,
        since=since,
        count=count,
        fmt=fmt,
        apply_filter=apply_filter,
    )
    if result.error:
        return result.error
    return result.output


@mcp.tool()
async def ts4k_get(msg_id: str, fmt: str = "pipe") -> str:
    """Read a single message by its prefixed ID.

    Args:
        msg_id: Message ID with source prefix (e.g. "g:18f6a2b3c4e5f6a7").
        fmt: Output format — "pipe" (default), "json", or "xml".
    """
    result = await commands.get_message(msg_id=msg_id, fmt=fmt)
    if result.error:
        return result.error
    return result.output


@mcp.tool()
async def ts4k_thread(thread_id: str, fmt: str = "pipe") -> str:
    """Read a thread or conversation by its prefixed ID.

    Args:
        thread_id: Thread/chat ID with source prefix (e.g. "g:18f6a2b3c4e5f6a8").
        fmt: Output format — "pipe" (default), "json", or "xml".
    """
    result = await commands.get_thread(thread_id=thread_id, fmt=fmt)
    if result.error:
        return result.error
    return result.output


@mcp.tool()
async def ts4k_list(
    source: str = "all",
    query: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    apply_filter: bool = False,
) -> str:
    """Search and list messages matching a query.

    Args:
        source: Source prefix, provider name, or "all".
        query: Search query string (provider-specific).
        count: Maximum messages to return (default 20).
        fmt: Output format — "pipe" (default), "json", or "xml".
        apply_filter: Apply configured skip filters (default off).
    """
    result = await commands.list_messages(
        source=source,
        query=query,
        count=count,
        fmt=fmt,
        apply_filter=apply_filter,
    )
    if result.error:
        return result.error
    return result.output


@mcp.tool()
def ts4k_status() -> str:
    """Show operational status: sources, watermarks, contacts, filters, stats."""
    return commands.get_status()


@mcp.tool()
def ts4k_contacts(
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
def ts4k_cache(action: str = "stats", source: str | None = None, stale_only: bool = False) -> str:
    """Manage the message cache.

    Args:
        action: "stats" (show cache info) or "clear" (purge cached messages).
        source: For clear, limit to this source prefix (e.g. "g", "o"). Default: all.
        stale_only: For clear, only remove entries from an older schema version.
    """
    return commands.manage_cache(action=action, source=source, stale_only=stale_only)


@mcp.tool()
def ts4k_filter(action: str = "show", value: str | None = None) -> str:
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

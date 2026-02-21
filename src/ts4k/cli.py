"""ts4k CLI — token-efficient messaging gateway for LLM agents.

Usage::

    ts4k wn                              # what's new since last check
    ts4k wn --since 2d                   # what's new in the last 2 days
    ts4k l -q "from:alice" -n 10         # list 10 messages matching query
    ts4k g g:18f6a2b3c4e5f6a7            # read a single message
    ts4k t g:18f6a2b3c4e5f6a8            # read a thread
    ts4k h                               # show status + help

Environment variables:
    TS4K_GMAIL_EMAIL       Google email address (required for Gmail)
    TS4K_GMAIL_MCP_URL     URL for upstream MCP server (default: http://localhost:51429/mcp)
    TS4K_CONFIG_DIR        Config directory (default: ~/.config/ts4k)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
from ts4k.core.format import format_listing, format_message, format_thread
from ts4k.core.normalize import normalize, normalize_headers
from ts4k.state import watermarks

logger = logging.getLogger("ts4k")

_DEFAULT_MCP_URL = "http://localhost:51429/mcp"


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def _resolve_gmail_config(args: argparse.Namespace) -> GmailAdapterConfig:
    """Build a GmailAdapterConfig from CLI args + env vars."""
    email = getattr(args, "email", None) or os.environ.get("TS4K_GMAIL_EMAIL")
    if not email:
        print(
            "Error: Gmail email required. Use --email or set TS4K_GMAIL_EMAIL.",
            file=sys.stderr,
        )
        sys.exit(1)

    transport = getattr(args, "transport", None) or "streamable-http"
    server_url = (
        getattr(args, "url", None)
        or os.environ.get("TS4K_GMAIL_MCP_URL")
        or _DEFAULT_MCP_URL
    )

    return GmailAdapterConfig(
        user_email=email,
        transport=transport,
        server_url=server_url,
    )


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------


def _normalize_message(msg: dict) -> dict:
    """Run the normalizer on a message dict (body + headers)."""
    result = dict(msg)

    if result.get("body"):
        result["body"] = normalize(result["body"])

    headers_to_norm = {}
    for key in ("from", "to", "cc", "date", "subject"):
        if key in result:
            headers_to_norm[key] = result[key]

    if headers_to_norm:
        normed = normalize_headers(headers_to_norm)
        result.update(normed)

    return result


def _normalize_thread(thread: dict) -> dict:
    """Normalize all messages in a thread."""
    result = dict(thread)
    result["messages"] = [_normalize_message(m) for m in thread.get("messages", [])]

    if result.get("subject"):
        normed = normalize_headers({"subject": result["subject"]})
        result["subject"] = normed.get("subject", result["subject"])

    return result


def _since_to_query(since: str | None) -> str:
    """Convert a --since value to a Gmail search query fragment.

    Accepts:
      - None → uses watermark, falls back to 1d
      - "2d", "7d", "30d" → newer_than:Xd
      - ISO timestamp → after:epoch
      - Gmail query fragment → passed through
    """
    if since is None:
        wm = watermarks.get("g")
        if wm:
            # Convert ISO to epoch for Gmail's after: operator
            from datetime import datetime, timezone
            try:
                dt = datetime.fromisoformat(wm.replace("Z", "+00:00"))
                return f"after:{int(dt.timestamp())}"
            except ValueError:
                return "newer_than:1d"
        return "newer_than:1d"

    # Short form: "2d", "7d", etc.
    if since.endswith("d") and since[:-1].isdigit():
        return f"newer_than:{since}"

    # Try as ISO timestamp
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return f"after:{int(dt.timestamp())}"
    except ValueError:
        pass

    # Pass through as-is (Gmail query fragment)
    return since


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    """Handle the whatsnew / wn command."""
    config = _resolve_gmail_config(args)
    fmt = getattr(args, "format", "pipe") or "pipe"
    count = getattr(args, "count", 20) or 20
    since = getattr(args, "since", None)

    query = _since_to_query(since)

    async with GmailAdapter(config) as adapter:
        listing = await adapter.list_messages(query=query, count=count)

        if not listing:
            print("No new messages.", file=sys.stderr)
            return

        listing = listing[:count]

        messages = []
        for entry in listing:
            try:
                msg = await adapter.read_message(entry["id"])
                msg = _normalize_message(msg)
                messages.append(msg)
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", entry["id"], exc)

        if not messages:
            print("No messages could be fetched.", file=sys.stderr)
            return

        print(format_listing(messages, fmt=fmt))

        # Update watermark to newest message date — using wn IS the side effect
        newest = max(
            (m.get("date", "") for m in messages),
            default=None,
        )
        if newest:
            watermarks.update("g", newest)


async def _cmd_get(args: argparse.Namespace) -> None:
    """Handle the get / g command."""
    config = _resolve_gmail_config(args)
    fmt = getattr(args, "format", "pipe") or "pipe"
    msg_id = args.id

    async with GmailAdapter(config) as adapter:
        msg = await adapter.read_message(msg_id)
        msg = _normalize_message(msg)
        print(format_message(msg, fmt=fmt))


async def _cmd_thread(args: argparse.Namespace) -> None:
    """Handle the thread / t command."""
    config = _resolve_gmail_config(args)
    fmt = getattr(args, "format", "pipe") or "pipe"
    thread_id = args.id

    async with GmailAdapter(config) as adapter:
        thread = await adapter.read_thread(thread_id)
        thread = _normalize_thread(thread)
        print(format_thread(thread, fmt=fmt))


async def _cmd_list(args: argparse.Namespace) -> None:
    """Handle the list / l command."""
    config = _resolve_gmail_config(args)
    fmt = getattr(args, "format", "pipe") or "pipe"
    count = getattr(args, "count", 20) or 20
    query = getattr(args, "query", None)

    async with GmailAdapter(config) as adapter:
        listing = await adapter.list_messages(query=query, count=count)

        if not listing:
            print("No messages found.", file=sys.stderr)
            return

        messages = []
        for entry in listing:
            try:
                msg = await adapter.read_message(entry["id"])
                msg = _normalize_message(msg)
                messages.append(msg)
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", entry["id"], exc)

        if not messages:
            print("No messages could be fetched.", file=sys.stderr)
            return

        print(format_listing(messages, fmt=fmt))


def _cmd_help(args: argparse.Namespace) -> None:
    """Handle the help / h command — show status and quick reference."""
    wm = watermarks.all()
    config_dir = os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")
    email = os.environ.get("TS4K_GMAIL_EMAIL", "(not set)")
    url = os.environ.get("TS4K_GMAIL_MCP_URL", _DEFAULT_MCP_URL)

    print("ts4k — Token Saver 4000")
    print()
    print("Commands:")
    print("  wn [--since 2d]           What's new (updates watermark)")
    print("  l [-q QUERY] [-n COUNT]   List messages")
    print("  g MSG_ID                  Read a single message")
    print("  t THREAD_ID              Read a thread")
    print("  h                         This help + status")
    print()
    print("Status:")
    print(f"  Email:  {email}")
    print(f"  MCP:    {url}")
    print(f"  Config: {config_dir}")
    if wm:
        for src, ts in sorted(wm.items()):
            print(f"  Watermark [{src}]: {ts}")
    else:
        print("  Watermarks: (none — run wn to set)")
    print()
    print("Formats: -f p(ipe) | j(son) | x(ml)")


async def _cmd_skill(args: argparse.Namespace) -> None:
    """Handle the skill command — machine-readable output for Claude Code."""
    # Skill mode is a thin wrapper: runs the specified subcommand
    # with pipe format and returns the result.  This is the entry point
    # that a Claude Code skill stub calls.
    subcmd = getattr(args, "subcmd", None)
    if not subcmd:
        print("Usage: ts4k skill <wn|l|g|t> [args...]", file=sys.stderr)
        sys.exit(1)

    # Re-parse remaining args as the subcommand
    argv = [subcmd] + (getattr(args, "skill_args", None) or [])
    # Force pipe format for skill mode
    argv.extend(["-f", "pipe"])
    parser = _build_parser()
    sub_args = parser.parse_args(argv)

    if not hasattr(sub_args, "func") or sub_args.func is None:
        print(f"Unknown skill subcommand: {subcmd}", file=sys.stderr)
        sys.exit(1)

    await sub_args.func(sub_args)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add args shared across commands that hit the MCP server."""
    parser.add_argument("--email", help="Google email (or set TS4K_GMAIL_EMAIL)")
    parser.add_argument("--url", help=f"MCP server URL (default: {_DEFAULT_MCP_URL})")
    parser.add_argument(
        "--transport", default="streamable-http",
        help="MCP transport: stdio or streamable-http (default: streamable-http)",
    )
    parser.add_argument(
        "-f", "--format", default="pipe",
        help="Output format: pipe, json, xml (or p, j, x)",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the ts4k CLI."""
    parser = argparse.ArgumentParser(
        prog="ts4k",
        description="Token-efficient messaging gateway for LLM agents.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- whatsnew / wn ---
    for cmd_name in ("whatsnew", "wn"):
        wn = subparsers.add_parser(cmd_name, help="Show new messages (updates watermark)")
        wn.add_argument("--since", help="Time range: 2d, 7d, ISO timestamp, or Gmail query")
        wn.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
        _add_common_args(wn)
        wn.set_defaults(func=_cmd_whatsnew)

    # --- get / g ---
    for cmd_name in ("get", "g"):
        get = subparsers.add_parser(cmd_name, help="Read a single message")
        get.add_argument("id", help="Message ID (e.g. g:abc123)")
        _add_common_args(get)
        get.set_defaults(func=_cmd_get)

    # --- thread / t ---
    for cmd_name in ("thread", "t"):
        th = subparsers.add_parser(cmd_name, help="Read a thread")
        th.add_argument("id", help="Thread ID (e.g. g:abc123)")
        _add_common_args(th)
        th.set_defaults(func=_cmd_thread)

    # --- list / l ---
    for cmd_name in ("list", "l"):
        ls = subparsers.add_parser(cmd_name, help="List messages matching a query")
        ls.add_argument("--query", "-q", help="Gmail search query")
        ls.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
        _add_common_args(ls)
        ls.set_defaults(func=_cmd_list)

    # --- help / h ---
    for cmd_name in ("help", "h"):
        hp = subparsers.add_parser(cmd_name, help="Show status and quick reference")
        hp.set_defaults(func=_cmd_help)

    # --- skill ---
    sk = subparsers.add_parser("skill", help="Machine-readable output for Claude Code")
    sk.add_argument("subcmd", nargs="?", help="Subcommand: wn, l, g, t")
    sk.add_argument("skill_args", nargs="*", help="Arguments for the subcommand")
    sk.set_defaults(func=_cmd_skill)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.  Called by the ``ts4k`` console script."""
    # Ensure UTF-8 output on Windows
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    if not hasattr(args, "func") or args.func is None:
        parser.print_help()
        sys.exit(1)

    # help/h is sync, everything else is async
    if args.func is _cmd_help:
        args.func(args)
        return

    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

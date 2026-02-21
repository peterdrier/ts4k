"""ts4k CLI — token-efficient messaging gateway for LLM agents.

Usage::

    ts4k whatsnew --source gmail --format pipe
    ts4k wn -f xml --count 5
    ts4k get g:18f6a2b3c4e5f6a7 --format json
    ts4k thread g:18f6a2b3c4e5f6a8 --format xml
    ts4k list --query "from:alice" --source gmail --count 10

Environment variables:
    TS4K_GMAIL_EMAIL       Google email address (required for Gmail)
    TS4K_GMAIL_MCP_CWD     Working directory for the upstream MCP server
    TS4K_GMAIL_MCP_URL     URL for HTTP mode (streamable-http)
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

logger = logging.getLogger("ts4k")


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

    transport = getattr(args, "transport", None) or "stdio"
    server_cwd = os.environ.get("TS4K_GMAIL_MCP_CWD")
    server_url = os.environ.get("TS4K_GMAIL_MCP_URL", "http://localhost:8000/mcp")

    return GmailAdapterConfig(
        user_email=email,
        transport=transport,
        server_cwd=server_cwd,
        server_url=server_url,
    )


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------


def _normalize_message(msg: dict) -> dict:
    """Run the normalizer on a message dict (body + headers)."""
    result = dict(msg)

    # Normalize body
    if result.get("body"):
        result["body"] = normalize(result["body"])

    # Normalize headers
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

    # Normalize thread-level subject
    if result.get("subject"):
        normed = normalize_headers({"subject": result["subject"]})
        result["subject"] = normed.get("subject", result["subject"])

    return result


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    """Handle the whatsnew / wn command."""
    config = _resolve_gmail_config(args)
    fmt = getattr(args, "format", "pipe") or "pipe"
    count = getattr(args, "count", 20) or 20
    since = getattr(args, "since", None)

    async with GmailAdapter(config) as adapter:
        # Step 1: Get list of message IDs
        listing = await adapter.whatsnew(since=since)

        if not listing:
            print("No new messages.", file=sys.stderr)
            return

        # Limit to count
        listing = listing[:count]

        # Step 2: Fetch full details for each message
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

        # Step 3: Format and output
        print(format_listing(messages, fmt=fmt))


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

        # Fetch full details for each message
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


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


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
        wn = subparsers.add_parser(cmd_name, help="Show new messages")
        wn.add_argument("--source", default="gmail", help="Message source (default: gmail)")
        wn.add_argument("--since", help="ISO timestamp or Gmail after: value")
        wn.add_argument("-f", "--format", default="pipe", help="Output format: pipe, json, xml (or p, j, x)")
        wn.add_argument("--count", "-n", type=int, default=20, help="Max messages to return")
        wn.add_argument("--email", help="Google email address")
        wn.add_argument("--transport", default="stdio", help="MCP transport: stdio or streamable-http")
        wn.set_defaults(func=_cmd_whatsnew)

    # --- get / g ---
    for cmd_name in ("get", "g"):
        get = subparsers.add_parser(cmd_name, help="Read a single message")
        get.add_argument("id", help="Message ID (e.g. g:abc123)")
        get.add_argument("-f", "--format", default="pipe", help="Output format: pipe, json, xml (or p, j, x)")
        get.add_argument("--full", action="store_true", help="Include full message content")
        get.add_argument("--email", help="Google email address")
        get.add_argument("--transport", default="stdio", help="MCP transport: stdio or streamable-http")
        get.set_defaults(func=_cmd_get)

    # --- thread / t ---
    for cmd_name in ("thread", "t"):
        th = subparsers.add_parser(cmd_name, help="Read a thread")
        th.add_argument("id", help="Thread ID (e.g. g:abc123)")
        th.add_argument("-f", "--format", default="pipe", help="Output format: pipe, json, xml (or p, j, x)")
        th.add_argument("--email", help="Google email address")
        th.add_argument("--transport", default="stdio", help="MCP transport: stdio or streamable-http")
        th.set_defaults(func=_cmd_thread)

    # --- list / l ---
    for cmd_name in ("list", "l"):
        ls = subparsers.add_parser(cmd_name, help="List messages matching a query")
        ls.add_argument("--query", "-q", help="Gmail search query")
        ls.add_argument("--source", default="gmail", help="Message source (default: gmail)")
        ls.add_argument("-f", "--format", default="pipe", help="Output format: pipe, json, xml (or p, j, x)")
        ls.add_argument("--count", "-n", type=int, default=20, help="Max messages to return")
        ls.add_argument("--email", help="Google email address")
        ls.add_argument("--transport", default="stdio", help="MCP transport: stdio or streamable-http")
        ls.set_defaults(func=_cmd_list)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.  Called by the ``ts4k`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    if not hasattr(args, "func") or args.func is None:
        parser.print_help()
        sys.exit(1)

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

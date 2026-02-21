"""ts4k CLI — token-efficient messaging gateway for LLM agents.

Usage::

    ts4k wn                              # what's new across all sources
    ts4k wn --source gmail               # what's new in Gmail only
    ts4k wn --since 2d                   # what's new in the last 2 days
    ts4k l -q "from:alice" -n 10         # list 10 messages matching query
    ts4k g g:18f6a2b3c4e5f6a7            # read a Gmail message
    ts4k g w:3EB05C4245618036            # read a WhatsApp message
    ts4k t g:18f6a2b3c4e5f6a8            # read a Gmail thread
    ts4k t w:34620225091@s.whatsapp.net  # read a WhatsApp chat
    ts4k h                               # show status + help

Environment variables:
    TS4K_GMAIL_EMAIL       Google email address (required for Gmail)
    TS4K_GMAIL_MCP_URL     URL for Gmail MCP server (default: http://localhost:51429/mcp)
    TS4K_WA_MCP_CWD        WhatsApp MCP server directory
    TS4K_CONFIG_DIR        Config directory (default: ~/.config/ts4k)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
from ts4k.adapters.whatsapp import WhatsAppAdapter, WhatsAppAdapterConfig
from ts4k.core.format import format_listing, format_message, format_thread
from ts4k.core.normalize import normalize, normalize_headers
from ts4k.state import contacts, watermarks

logger = logging.getLogger("ts4k")

_DEFAULT_GMAIL_MCP_URL = "http://localhost:51429/mcp"
_DEFAULT_WA_MCP_CWD = "~/whatsapp-mcp/server"


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def _resolve_gmail_config(args: argparse.Namespace) -> GmailAdapterConfig | None:
    """Build a GmailAdapterConfig, or None if not configured."""
    email = getattr(args, "email", None) or os.environ.get("TS4K_GMAIL_EMAIL")
    if not email:
        return None

    transport = getattr(args, "transport", None) or "streamable-http"
    server_url = (
        getattr(args, "url", None)
        or os.environ.get("TS4K_GMAIL_MCP_URL")
        or _DEFAULT_GMAIL_MCP_URL
    )

    return GmailAdapterConfig(
        user_email=email,
        transport=transport,
        server_url=server_url,
    )


def _resolve_wa_config(args: argparse.Namespace) -> WhatsAppAdapterConfig | None:
    """Build a WhatsAppAdapterConfig, or None if not configured."""
    cwd = os.environ.get("TS4K_WA_MCP_CWD", _DEFAULT_WA_MCP_CWD)
    if not os.path.isdir(cwd):
        return None
    return WhatsAppAdapterConfig(server_cwd=cwd)


def _resolve_sources(args: argparse.Namespace) -> list[str]:
    """Determine which sources to query based on --source flag."""
    source = getattr(args, "source", None) or "all"
    source = source.lower()
    if source in ("gmail", "g"):
        return ["gmail"]
    if source in ("whatsapp", "wa", "w"):
        return ["whatsapp"]
    if source == "all":
        return ["gmail", "whatsapp"]
    return [source]


def _source_from_id(prefixed_id: str) -> str:
    """Infer source from a prefixed ID like 'g:xxx' or 'w:xxx'."""
    if prefixed_id.startswith("g:"):
        return "gmail"
    if prefixed_id.startswith("w:"):
        return "whatsapp"
    return "gmail"  # default


def _require_gmail_config(args: argparse.Namespace) -> GmailAdapterConfig:
    """Like _resolve_gmail_config but exits on failure."""
    config = _resolve_gmail_config(args)
    if config is None:
        print(
            "Error: Gmail email required. Use --email or set TS4K_GMAIL_EMAIL.",
            file=sys.stderr,
        )
        sys.exit(1)
    return config


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


def _since_to_gmail_query(since: str | None) -> str:
    """Convert a --since value to a Gmail search query fragment."""
    if since is None:
        wm = watermarks.get("g")
        if wm:
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(wm.replace("Z", "+00:00"))
                return f"after:{int(dt.timestamp())}"
            except ValueError:
                return "newer_than:1d"
        return "newer_than:1d"

    if since.endswith("d") and since[:-1].isdigit():
        return f"newer_than:{since}"

    try:
        from datetime import datetime
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return f"after:{int(dt.timestamp())}"
    except ValueError:
        pass

    return since


def _since_to_iso(since: str | None, source: str) -> str | None:
    """Convert a --since value to an ISO timestamp for adapters that take ISO."""
    if since is None:
        return watermarks.get(source[0])  # "g" or "w"

    if since.endswith("d") and since[:-1].isdigit():
        from datetime import datetime, timedelta, timezone
        days = int(since[:-1])
        dt = datetime.now(timezone.utc) - timedelta(days=days)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return since


# ---------------------------------------------------------------------------
# Parallel adapter helpers
# ---------------------------------------------------------------------------


async def _fetch_gmail_whatsnew(
    args: argparse.Namespace, since: str | None, count: int
) -> list[dict]:
    """Fetch new Gmail messages. Returns normalized message dicts."""
    config = _resolve_gmail_config(args)
    if config is None:
        return []

    query = _since_to_gmail_query(since)
    try:
        async with GmailAdapter(config) as adapter:
            listing = await adapter.list_messages(query=query, count=count)
            if not listing:
                return []

            messages = []
            for entry in listing[:count]:
                try:
                    msg = await adapter.read_message(entry["id"])
                    msg = _normalize_message(msg)
                    msg.setdefault("source", "g")
                    messages.append(msg)
                except Exception as exc:
                    logger.warning("Gmail fetch %s: %s", entry["id"], exc)
            return messages
    except Exception as exc:
        logger.warning("Gmail adapter failed: %s", exc)
        return []


async def _fetch_wa_whatsnew(
    args: argparse.Namespace, since: str | None, count: int
) -> list[dict]:
    """Fetch new WhatsApp messages. Returns normalized message dicts."""
    config = _resolve_wa_config(args)
    if config is None:
        return []

    iso_since = _since_to_iso(since, "whatsapp")
    try:
        async with WhatsAppAdapter(config) as adapter:
            listing = await adapter.whatsnew(since=iso_since)
            if not listing:
                return []

            messages = []
            for entry in listing[:count]:
                msg = _normalize_message(entry)
                msg.setdefault("source", "w")
                messages.append(msg)
            return messages
    except Exception as exc:
        logger.warning("WhatsApp adapter failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    """Handle the whatsnew / wn command."""
    fmt = getattr(args, "format", "pipe") or "pipe"
    count = getattr(args, "count", 20) or 20
    since = getattr(args, "since", None)
    sources = _resolve_sources(args)

    # Build fetch tasks per source
    tasks: list[asyncio.Task] = []
    source_names: list[str] = []

    if "gmail" in sources:
        tasks.append(asyncio.create_task(_fetch_gmail_whatsnew(args, since, count)))
        source_names.append("gmail")

    if "whatsapp" in sources:
        tasks.append(asyncio.create_task(_fetch_wa_whatsnew(args, since, count)))
        source_names.append("whatsapp")

    # Run in parallel
    results = await asyncio.gather(*tasks)

    # Merge and sort by date descending
    all_messages: list[dict] = []
    for msgs in results:
        all_messages.extend(msgs)

    all_messages.sort(key=lambda m: m.get("date", ""), reverse=True)
    all_messages = all_messages[:count]

    if not all_messages:
        print("No new messages.", file=sys.stderr)
        return

    print(format_listing(all_messages, fmt=fmt))

    # Update watermarks per source
    for source_name, msgs in zip(source_names, results):
        if msgs:
            prefix = "g" if source_name == "gmail" else "w"
            newest = max(m.get("date", "") for m in msgs)
            if newest:
                watermarks.update(prefix, newest)


async def _cmd_get(args: argparse.Namespace) -> None:
    """Handle the get / g command."""
    fmt = getattr(args, "format", "pipe") or "pipe"
    msg_id = args.id
    source = _source_from_id(msg_id)

    if source == "whatsapp":
        config = _resolve_wa_config(args)
        if config is None:
            print("Error: WhatsApp MCP not configured.", file=sys.stderr)
            sys.exit(1)
        async with WhatsAppAdapter(config) as adapter:
            msg = await adapter.read_message(msg_id)
            msg = _normalize_message(msg)
            print(format_message(msg, fmt=fmt))
    else:
        config = _require_gmail_config(args)
        async with GmailAdapter(config) as adapter:
            msg = await adapter.read_message(msg_id)
            msg = _normalize_message(msg)
            print(format_message(msg, fmt=fmt))


async def _cmd_thread(args: argparse.Namespace) -> None:
    """Handle the thread / t command."""
    fmt = getattr(args, "format", "pipe") or "pipe"
    thread_id = args.id
    source = _source_from_id(thread_id)

    if source == "whatsapp":
        config = _resolve_wa_config(args)
        if config is None:
            print("Error: WhatsApp MCP not configured.", file=sys.stderr)
            sys.exit(1)
        async with WhatsAppAdapter(config) as adapter:
            thread = await adapter.read_thread(thread_id)
            thread = _normalize_thread(thread)
            print(format_thread(thread, fmt=fmt))
    else:
        config = _require_gmail_config(args)
        async with GmailAdapter(config) as adapter:
            thread = await adapter.read_thread(thread_id)
            thread = _normalize_thread(thread)
            print(format_thread(thread, fmt=fmt))


async def _cmd_list(args: argparse.Namespace) -> None:
    """Handle the list / l command."""
    fmt = getattr(args, "format", "pipe") or "pipe"
    count = getattr(args, "count", 20) or 20
    query = getattr(args, "query", None)
    sources = _resolve_sources(args)

    all_messages: list[dict] = []

    if "gmail" in sources:
        config = _resolve_gmail_config(args)
        if config:
            try:
                async with GmailAdapter(config) as adapter:
                    listing = await adapter.list_messages(query=query, count=count)
                    for entry in (listing or []):
                        try:
                            msg = await adapter.read_message(entry["id"])
                            msg = _normalize_message(msg)
                            msg.setdefault("source", "g")
                            all_messages.append(msg)
                        except Exception as exc:
                            logger.warning("Gmail fetch %s: %s", entry["id"], exc)
            except Exception as exc:
                logger.warning("Gmail adapter failed: %s", exc)

    if "whatsapp" in sources:
        config = _resolve_wa_config(args)
        if config:
            try:
                async with WhatsAppAdapter(config) as adapter:
                    listing = await adapter.list_messages(query=query, count=count)
                    for entry in (listing or []):
                        msg = _normalize_message(entry)
                        msg.setdefault("source", "w")
                        all_messages.append(msg)
            except Exception as exc:
                logger.warning("WhatsApp adapter failed: %s", exc)

    if not all_messages:
        print("No messages found.", file=sys.stderr)
        return

    all_messages.sort(key=lambda m: m.get("date", ""), reverse=True)
    print(format_listing(all_messages[:count], fmt=fmt))


def _cmd_help(args: argparse.Namespace) -> None:
    """Handle the help / h command — show status and quick reference."""
    wm = watermarks.all()
    config_dir = os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")
    email = os.environ.get("TS4K_GMAIL_EMAIL", "(not set)")
    gmail_url = os.environ.get("TS4K_GMAIL_MCP_URL", _DEFAULT_GMAIL_MCP_URL)
    wa_cwd = os.environ.get("TS4K_WA_MCP_CWD", _DEFAULT_WA_MCP_CWD)
    wa_ok = os.path.isdir(wa_cwd)

    print("ts4k — Token Saver 4000")
    print()
    print("Commands:")
    print("  wn [--since 2d] [--source gmail|wa|all]   What's new (updates watermark)")
    print("  l [-q QUERY] [-n COUNT] [--source ...]    List messages")
    print("  g MSG_ID                                  Read a message (g: or w: prefix)")
    print("  t THREAD_ID                               Read a thread/chat")
    print("  c link ALIAS ID [ID...]                   Link identifiers to a contact")
    print("  c unlink ALIAS [ID...]                    Unlink identifiers or remove contact")
    print("  c find TERM                               Search contacts")
    print("  c list                                    List all contacts")
    print("  h                                         This help + status")
    print()
    print("Sources:")
    print(f"  Gmail:    {email} -> {gmail_url}")
    print(f"  WhatsApp: {'ok' if wa_ok else 'not found'} ({wa_cwd})")
    print(f"  Config:   {config_dir}")
    if wm:
        for src, ts in sorted(wm.items()):
            label = {"g": "Gmail", "w": "WhatsApp"}.get(src, src)
            print(f"  Watermark [{label}]: {ts}")
    else:
        print("  Watermarks: (none — run wn to set)")
    print()
    print("Formats: -f p(ipe) | j(son) | x(ml)")


def _cmd_contacts(args: argparse.Namespace) -> None:
    """Handle the contacts / c command."""
    action = getattr(args, "action", None)

    if action == "link":
        alias = args.alias
        idents = args.identifiers
        if not idents:
            print("Error: at least one identifier required.", file=sys.stderr)
            sys.exit(1)
        result = contacts.link(alias, *idents)
        print(f"{alias}: {' | '.join(result)}")

    elif action == "unlink":
        alias = args.alias
        idents = args.identifiers
        if idents:
            result = contacts.unlink(alias, *idents)
            if result is None:
                print(f"{alias}: (removed)")
            else:
                print(f"{alias}: {' | '.join(result)}")
        else:
            contacts.unlink(alias)
            print(f"{alias}: (removed)")

    elif action == "find":
        term = args.term
        results = contacts.find(term)
        if not results:
            print("No matches.", file=sys.stderr)
            return
        for alias, idents in sorted(results.items()):
            print(f"{alias}: {' | '.join(idents)}")

    elif action == "list":
        all_contacts = contacts.list_all()
        if not all_contacts:
            print("No contacts.", file=sys.stderr)
            return
        for alias, idents in sorted(all_contacts.items()):
            print(f"{alias}: {' | '.join(idents)}")

    else:
        # Default: same as list
        all_contacts = contacts.list_all()
        if not all_contacts:
            print("No contacts. Use 'ts4k c link <alias> <id> [<id>...]' to add.", file=sys.stderr)
            return
        for alias, idents in sorted(all_contacts.items()):
            print(f"{alias}: {' | '.join(idents)}")


async def _cmd_skill(args: argparse.Namespace) -> None:
    """Handle the skill command — machine-readable output for Claude Code."""
    subcmd = getattr(args, "subcmd", None)
    if not subcmd:
        print("Usage: ts4k skill <wn|l|g|t> [args...]", file=sys.stderr)
        sys.exit(1)

    argv = [subcmd] + (getattr(args, "skill_args", None) or [])
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
    """Add args shared across commands that hit MCP servers."""
    parser.add_argument("--email", help="Google email (or set TS4K_GMAIL_EMAIL)")
    parser.add_argument("--url", help=f"Gmail MCP URL (default: {_DEFAULT_GMAIL_MCP_URL})")
    parser.add_argument(
        "--transport", default="streamable-http",
        help="Gmail MCP transport (default: streamable-http)",
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
        wn.add_argument("--source", "-s", default="all", help="Source: gmail, whatsapp, all (default: all)")
        _add_common_args(wn)
        wn.set_defaults(func=_cmd_whatsnew)

    # --- get / g ---
    for cmd_name in ("get", "g"):
        get = subparsers.add_parser(cmd_name, help="Read a single message")
        get.add_argument("id", help="Message ID (e.g. g:abc123 or w:3EB05C)")
        _add_common_args(get)
        get.set_defaults(func=_cmd_get)

    # --- thread / t ---
    for cmd_name in ("thread", "t"):
        th = subparsers.add_parser(cmd_name, help="Read a thread or chat")
        th.add_argument("id", help="Thread/chat ID (e.g. g:abc123 or w:jid@s.whatsapp.net)")
        _add_common_args(th)
        th.set_defaults(func=_cmd_thread)

    # --- list / l ---
    for cmd_name in ("list", "l"):
        ls = subparsers.add_parser(cmd_name, help="List messages matching a query")
        ls.add_argument("--query", "-q", help="Search query")
        ls.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
        ls.add_argument("--source", "-s", default="all", help="Source: gmail, whatsapp, all (default: all)")
        _add_common_args(ls)
        ls.set_defaults(func=_cmd_list)

    # --- contacts / c ---
    for cmd_name in ("contacts", "c"):
        ct = subparsers.add_parser(cmd_name, help="Cross-platform contact identity map")
        ct_sub = ct.add_subparsers(dest="action")

        ct_link = ct_sub.add_parser("link", help="Link identifiers to an alias")
        ct_link.add_argument("alias", help="Contact alias (e.g. sarah)")
        ct_link.add_argument("identifiers", nargs="+", help="Platform IDs (e.g. g:sarah@gmail.com w:123@wa)")

        ct_unlink = ct_sub.add_parser("unlink", help="Unlink identifiers or remove alias")
        ct_unlink.add_argument("alias", help="Contact alias")
        ct_unlink.add_argument("identifiers", nargs="*", help="IDs to remove (omit to delete alias)")

        ct_sub.add_parser("list", help="List all contacts")

        ct_find = ct_sub.add_parser("find", help="Search contacts")
        ct_find.add_argument("term", help="Search term (matches alias or identifier)")

        ct.set_defaults(func=_cmd_contacts)

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

    if args.func in (_cmd_help, _cmd_contacts):
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

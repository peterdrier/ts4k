"""ts4k CLI — token-efficient messaging gateway for LLM agents.

Usage::

    ts4k wn                              # what's new across all sources
    ts4k wn --source g                   # what's new from source "g" only
    ts4k wn --source gmail               # what's new from all Gmail sources
    ts4k wn --since 2d                   # what's new in the last 2 days
    ts4k l -q "from:alice" -n 10         # list 10 messages matching query
    ts4k g g:18f6a2b3c4e5f6a7            # read a Gmail message
    ts4k g w:3EB05C4245618036            # read a WhatsApp message
    ts4k t g:18f6a2b3c4e5f6a8            # read a Gmail thread
    ts4k t w:34620225091@s.whatsapp.net  # read a WhatsApp chat
    ts4k src list                        # show configured sources
    ts4k src add g gmail email=x@y.com   # add a source
    ts4k h                               # help + quick reference

Sources are configured in ~/.config/ts4k/sources.json.  Each source has a
user-chosen prefix (e.g. "g", "gn", "w") that namespaces all its message IDs.

Environment variables:
    TS4K_CONFIG_DIR        Config directory (default: ~/.config/ts4k)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

from ts4k import commands
from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig
from ts4k.state import sources, watermarks


# ---------------------------------------------------------------------------
# Command handlers — thin wrappers around commands.*
# ---------------------------------------------------------------------------


async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    result = await commands.whatsnew(
        source=getattr(args, "source", None),
        since=getattr(args, "since", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        apply_filter=getattr(args, "filter", False),
    )
    if result.error:
        print(result.error, file=sys.stderr)
        return
    print(result.output)


async def _cmd_get(args: argparse.Namespace) -> None:
    result = await commands.get_message(
        msg_id=args.id,
        fmt=getattr(args, "format", "pipe") or "pipe",
    )
    if result.error:
        print(result.error, file=sys.stderr)
        sys.exit(1)
    print(result.output)


async def _cmd_thread(args: argparse.Namespace) -> None:
    result = await commands.get_thread(
        thread_id=args.id,
        fmt=getattr(args, "format", "pipe") or "pipe",
    )
    if result.error:
        print(result.error, file=sys.stderr)
        sys.exit(1)
    print(result.output)


async def _cmd_list(args: argparse.Namespace) -> None:
    result = await commands.list_messages(
        source=getattr(args, "source", None),
        query=getattr(args, "query", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        apply_filter=getattr(args, "filter", False),
    )
    if result.error:
        print(result.error, file=sys.stderr)
        return
    print(result.output)


def _cmd_help(args: argparse.Namespace) -> None:
    """Handle the help / h command — show status and quick reference."""
    config_dir = os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")
    all_cfg = sources.list_all()
    wm = watermarks.all()

    print("ts4k — Token Saver 4000")
    print()
    print("Commands:")
    print("  wn [--since 2d] [--source PREFIX|all]     What's new (updates watermark)")
    print("  l [-q QUERY] [-n COUNT] [--source ...]    List messages")
    print("  g MSG_ID                                  Read a message (prefix:id)")
    print("  t THREAD_ID                               Read a thread/chat")
    print("  src list|add|rm                           Manage sources")
    print("  c link|unlink|find|list                   Manage contacts")
    print("  f show|add-*|rm-*|reset                   Manage filters")
    print("  preload --source g [--query Q]             Paginate history into cache")
    print("  cache stats|clear                         Manage message cache")
    print("  st                                        Status, stats, efficiency")
    print("  h                                         This help")
    print()
    print("Flags: -F applies filters (off by default), -f p|j|x sets format")
    print()
    print("Sources:")
    if all_cfg:
        for prefix, cfg in sorted(all_cfg.items()):
            provider = cfg.get("provider", "?")
            detail = cfg.get("email") or cfg.get("mailbox") or cfg.get("mcp_cwd") or ""
            wm_ts = wm.get(prefix, "")
            wm_str = f"  wm:{wm_ts}" if wm_ts else ""
            print(f"  {prefix}: {provider} ({detail}){wm_str}")
    else:
        print("  (none — run: ts4k src add <prefix> <provider> ...)")
    print()
    print(f"Config: {config_dir}")


def _cmd_contacts(args: argparse.Namespace) -> None:
    output = commands.manage_contacts(
        action=getattr(args, "action", None),
        alias=getattr(args, "alias", None),
        identifiers=getattr(args, "identifiers", None),
        term=getattr(args, "term", None),
    )
    print(output)


def _cmd_status(args: argparse.Namespace) -> None:
    print(commands.get_status())


def _cmd_sources(args: argparse.Namespace) -> None:
    """Handle the src command — manage source config."""
    action = getattr(args, "action", None)

    if action == "add":
        prefix = args.prefix
        provider = args.provider.lower()
        kwargs: dict[str, Any] = {}
        for kv in (args.params or []):
            if "=" in kv:
                k, v = kv.split("=", 1)
                kwargs[k.strip()] = v.strip()

        entry = sources.add(prefix, provider=provider, **kwargs)
        print(f"Added source {prefix!r}:")
        for k, v in sorted(entry.items()):
            print(f"  {k}: {v}")

    elif action == "rm":
        prefix = args.prefix
        if sources.remove(prefix):
            print(f"Removed source {prefix!r}.")
        else:
            print(f"Source {prefix!r} not found.", file=sys.stderr)

    elif action == "list":
        all_cfg = sources.list_all()
        if not all_cfg:
            print("No sources configured.", file=sys.stderr)
            print("Add one:  ts4k src add g gmail email=you@gmail.com")
            return
        for prefix, cfg in sorted(all_cfg.items()):
            provider = cfg.get("provider", "?")
            detail = cfg.get("email") or cfg.get("mailbox") or cfg.get("mcp_cwd") or ""
            print(f"  {prefix}: {provider} ({detail})")
            for k, v in sorted(cfg.items()):
                if k not in ("provider", "email", "mailbox", "mcp_cwd"):
                    print(f"    {k}: {v}")

    elif action == "discover":
        asyncio.run(_cmd_discover_o365(args))

    else:
        _cmd_sources(argparse.Namespace(action="list", prefix=None, provider=None, params=None))


async def _cmd_discover_o365(args: argparse.Namespace) -> None:
    """Discover O365 mailboxes for the authenticated user."""
    all_cfg = sources.list_all()

    server_env: dict[str, str] | None = None
    for cfg in all_cfg.values():
        if cfg.get("provider", "").lower() == "o365":
            client_id = cfg.get("client_id")
            tenant_id = cfg.get("tenant_id")
            if client_id or tenant_id:
                server_env = {}
                if client_id:
                    server_env["MS365_MCP_CLIENT_ID"] = client_id
                if tenant_id:
                    server_env["MS365_MCP_TENANT_ID"] = tenant_id
                break

    adapter = O365Adapter(O365AdapterConfig(server_env=server_env), prefix="_discover")

    print("Discovering O365 mailboxes for authenticated user...")
    try:
        async with adapter:
            result = await adapter.discover_mailboxes()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    primary = result.get("primary", "")
    aliases = result.get("aliases", [])
    display_name = result.get("display_name", "")

    if not primary:
        print("  No mailbox found.", file=sys.stderr)
        return

    print(f"  User:     {display_name}")
    print(f"  Primary:  {primary}")
    if aliases:
        print(f"  Aliases:  {', '.join(aliases)}")
    else:
        print("  Aliases:  (none)")

    all_emails = [primary] + aliases
    existing_mailboxes = {
        cfg.get("mailbox", "").lower()
        for cfg in all_cfg.values()
        if cfg.get("provider", "").lower() == "o365"
    }
    used_prefixes = set(all_cfg.keys())

    print()
    print("Add as sources? (Each gets its own prefix)")

    next_suffix = ord("a")
    for email in all_emails:
        while True:
            candidate = f"o{chr(next_suffix)}"
            next_suffix += 1
            if candidate not in used_prefixes:
                break
        already = "  [already configured]" if email.lower() in existing_mailboxes else ""
        print(f"  {candidate} → {email}{already}")


async def _cmd_preload(args: argparse.Namespace) -> None:
    """Handle the preload command — paginate history into cache."""
    # Management actions
    if getattr(args, "status", False):
        print(commands.manage_preload("status"))
        return
    cancel_id = getattr(args, "cancel", None)
    if cancel_id:
        print(commands.manage_preload("cancel", cancel_id))
        return

    source = getattr(args, "source", None)
    if not source and not getattr(args, "resume", None):
        print("Error: --source is required (or --resume to continue a job).", file=sys.stderr)
        sys.exit(1)

    result = await commands.preload(
        source=source or "",
        query=getattr(args, "query", None),
        contact=getattr(args, "contact", None),
        since=getattr(args, "since", None),
        max_pages=getattr(args, "max_pages", 100) or 100,
        page_size=getattr(args, "page_size", 50) or 50,
        fetch_bodies=getattr(args, "bodies", False),
        resume_job=getattr(args, "resume", None),
        throttle=getattr(args, "throttle", 0.2) or 0.2,
    )
    print(result)


def _cmd_cache(args: argparse.Namespace) -> None:
    action = getattr(args, "action", None)
    source = getattr(args, "source", None)
    stale_only = getattr(args, "stale", False)
    print(commands.manage_cache(action=action, source=source, stale_only=stale_only))


def _cmd_filter(args: argparse.Namespace) -> None:
    action = getattr(args, "action", None)
    value = getattr(args, "value", None)

    # Default "show" action includes the usage hint
    if action is None:
        output = commands.manage_filters(action="show", value=value)
        print(output)
        print()
        print("Use -F flag on wn/l to apply. Filters are OFF by default.")
        return

    print(commands.manage_filters(action=action, value=value))


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
    """Add args shared across commands."""
    parser.add_argument(
        "-f", "--format", default="pipe",
        help="Output format: pipe, json, xml (or p, j, x)",
    )
    parser.add_argument(
        "-F", "--filter", action="store_true", default=False,
        help="Apply skip filters (off by default — unfiltered for triage)",
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
        wn.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all (default: all)")
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
        ls.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all (default: all)")
        _add_common_args(ls)
        ls.set_defaults(func=_cmd_list)

    # --- sources / src ---
    for cmd_name in ("sources", "src"):
        sr = subparsers.add_parser(cmd_name, help="Manage source config")
        sr_sub = sr.add_subparsers(dest="action")

        sr_add = sr_sub.add_parser("add", help="Add a source")
        sr_add.add_argument("prefix", help="Source prefix (e.g. g, gn, w)")
        sr_add.add_argument("provider", help="Provider: gmail, whatsapp, o365")
        sr_add.add_argument("params", nargs="*", help="Key=value pairs (e.g. email=x@y.com mcp_url=...)")

        sr_rm = sr_sub.add_parser("rm", help="Remove a source")
        sr_rm.add_argument("prefix", help="Source prefix to remove")

        sr_sub.add_parser("list", help="List all configured sources")

        sr_sub.add_parser("discover", help="Discover O365 mailboxes for authenticated user")

        sr.set_defaults(func=_cmd_sources)

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

    # --- filter / f ---
    for cmd_name in ("filter", "f"):
        fl = subparsers.add_parser(cmd_name, help="Manage skip filters (off by default)")
        fl_sub = fl.add_subparsers(dest="action")

        for action_name, help_text in [
            ("add-sender", "Add sender to skip list"),
            ("rm-sender", "Remove sender from skip list"),
            ("add-domain", "Add domain to skip list"),
            ("rm-domain", "Remove domain from skip list"),
            ("add-pattern", "Add regex pattern to skip"),
            ("rm-pattern", "Remove pattern from skip list"),
            ("skip-groups", "Set group chat skip (true/false)"),
        ]:
            sub = fl_sub.add_parser(action_name, help=help_text)
            sub.add_argument("value", help="Value to add/remove/set")

        fl_sub.add_parser("show", help="Show current filter config")
        fl_sub.add_parser("reset", help="Reset filters to defaults")

        fl.set_defaults(func=_cmd_filter)

    # --- preload ---
    pl = subparsers.add_parser("preload", help="Paginate through history into cache")
    pl.add_argument("--source", "-s", help="Source prefix or provider name")
    pl.add_argument("--query", "-q", help="Search query (provider-specific)")
    pl.add_argument("--contact", help="Contact alias — auto-expands to bidirectional query")
    pl.add_argument("--since", help="Start date: ISO timestamp or Nd shorthand")
    pl.add_argument("--max-pages", type=int, default=100, help="Max pages to fetch (default: 100)")
    pl.add_argument("--page-size", type=int, default=50, help="Messages per page (default: 50)")
    pl.add_argument("--bodies", action="store_true", help="Fetch full message bodies (slower)")
    pl.add_argument("--resume", metavar="JOB_ID", help="Resume an interrupted preload job")
    pl.add_argument("--throttle", type=float, default=0.2, help="Seconds between pages (default: 0.2)")
    pl.add_argument("--status", action="store_true", help="Show all preload jobs")
    pl.add_argument("--cancel", metavar="JOB_ID", help="Cancel a preload job")
    pl.set_defaults(func=_cmd_preload)

    # --- cache ---
    ca = subparsers.add_parser("cache", help="Manage message cache")
    ca_sub = ca.add_subparsers(dest="action")
    ca_sub.add_parser("stats", help="Show cache statistics")
    ca_clear = ca_sub.add_parser("clear", help="Clear cached messages")
    ca_clear.add_argument("--source", "-s", help="Clear only this source (e.g. g, o)")
    ca_clear.add_argument("--stale", action="store_true", help="Only clear stale (old schema) entries")
    ca.set_defaults(func=_cmd_cache)

    # --- status / st ---
    for cmd_name in ("status", "st"):
        st = subparsers.add_parser(cmd_name, help="Operational status, stats, efficiency")
        st.set_defaults(func=_cmd_status)

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

    if args.func in (_cmd_help, _cmd_contacts, _cmd_filter, _cmd_status, _cmd_sources, _cmd_cache):
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

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

from ts4k import commands, state
from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig
from ts4k.state import sources, watermarks
from ts4k.state.refs import RefTable


# ---------------------------------------------------------------------------
# Ref table helpers
# ---------------------------------------------------------------------------


def _refs_path() -> Path:
    """Path to the CLI refs file."""
    from pathlib import Path

    return state.get_config_dir().path / "refs.json"


def _new_ref_table() -> RefTable:
    """Create a fresh RefTable for CLI listing commands (last-listing-wins)."""
    return RefTable()


def _load_ref_table() -> RefTable:
    """Load the ref table from disk for CLI get/thread commands."""
    rt = RefTable()
    rt.load(_refs_path())
    return rt


# ---------------------------------------------------------------------------
# Command handlers — thin wrappers around commands.*
# ---------------------------------------------------------------------------


async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    refs = _new_ref_table()
    result = await commands.whatsnew(
        source=getattr(args, "source", None),
        since=getattr(args, "since", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        filter=getattr(args, "filter", False),
        ref_table=refs,
    )
    if result.error:
        print(result.error)
        return
    refs.save(_refs_path())
    print(result.output)


async def _cmd_get(args: argparse.Namespace) -> None:
    refs = _load_ref_table() if args.id.startswith("#") else None
    if args.id.startswith("#") and refs is not None and refs.resolve(args.id) is None:
        print(f"Ref {args.id} not found. Run 'wn' or 'l' first.")
        sys.exit(1)
    result = await commands.get_message(
        id=args.id,
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    if result.error:
        print(result.error)
        sys.exit(1)
    print(result.output)


async def _cmd_thread(args: argparse.Namespace) -> None:
    refs = _load_ref_table() if args.id.startswith("#") else None
    if args.id.startswith("#") and refs is not None and refs.resolve(args.id) is None:
        print(f"Ref {args.id} not found. Run 'wn' or 'l' first.")
        sys.exit(1)
    result = await commands.get_thread(
        tid=args.id,
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    if result.error:
        print(result.error)
        sys.exit(1)
    print(result.output)


async def _cmd_list(args: argparse.Namespace) -> None:
    refs = _new_ref_table()
    result = await commands.list_messages(
        source=getattr(args, "source", None),
        query=getattr(args, "query", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        filter=getattr(args, "filter", False),
        ref_table=refs,
    )
    if result.error:
        print(result.error)
        return
    refs.save(_refs_path())
    print(result.output)


def _cmd_help(args: argparse.Namespace) -> None:
    """Handle the help / h command — quick reference for commands and flags."""
    if getattr(args, "llm", False):
        print(commands.llm_help())
        return

    all_cfg = sources.list_all()

    print("ts4k — Token Saver 4000")
    print()
    print("Commands:")
    print("  updates [--since 2d] [--source S] [-n N]  What's new (updates watermark)  [wn]")
    print("  list [-q QUERY] [--source S] [-n N]       Search messages              [l]")
    print("  get ID                                    Read a message (prefix:id)   [g]")
    print("  thread TID                                Read a thread/chat           [t]")
    print("  overview [--source S] [--contact C]       Cache summary (drill-down)   [o]")
    print("  status                                    Health, stats, efficiency    [st]")
    print()
    print("  src list|add|rm                           Manage sources")
    print("  contacts link|unlink|find|list            Manage contacts              [c]")
    print("  filter show|add-*|rm-*|reset              Manage filters               [f]")
    print("  preload --source S [--query Q] [--bg]     Paginate history into cache")
    print("  cache stats|clear [--source S] [--stale]  Manage message cache")
    print("  auth gmail|o365                            Authenticate with a platform")
    print("  skill                                     Agent-oriented command reference")
    print()
    print("Flags: -F applies filters (off by default), -f p|j|x sets format")
    print("IDs:   g:xxx (Gmail), o:xxx (O365), w:xxx (WhatsApp)")

    if not all_cfg:
        print()
        print("Quick setup:")
        print("  1. ts4k src add g gmail email=you@gmail.com")
        print("  2. ts4k auth gmail you@gmail.com")
        print("  3. ts4k updates")


def _cmd_contacts(args: argparse.Namespace) -> None:
    output = commands.manage_contacts(
        action=getattr(args, "action", None),
        alias=getattr(args, "alias", None),
        identifiers=getattr(args, "identifiers", None),
        term=getattr(args, "term", None),
    )
    print(output)


def _cmd_status(args: argparse.Namespace) -> None:
    if getattr(args, "live", False):
        mbox = asyncio.run(
            commands.get_mailbox_stats(
                source=getattr(args, "source", None),
            )
        )
        print(commands.get_status(
            mailbox_stats_data=mbox,
            fmt=getattr(args, "format", "pipe") or "pipe",
        ))
    else:
        print(commands.get_status())


def _cmd_sources(args: argparse.Namespace) -> None:
    """Handle the src command — manage source config."""
    action = getattr(args, "action", None)

    if action == "add":
        prefix = args.prefix
        provider = args.provider.lower()
        kwargs: dict[str, Any] = {}
        # Fields that must be stored as lists (space-split from CLI string)
        _LIST_FIELDS = {"server_command"}
        for kv in (args.params or []):
            if "=" in kv:
                k, v = kv.split("=", 1)
                k, v = k.strip(), v.strip()
                if k in _LIST_FIELDS:
                    kwargs[k] = v.split()
                else:
                    kwargs[k] = v
            elif "@" in kv:
                # Bare email address — treat as email=value
                kwargs["email"] = kv.strip()

        # For O365: inherit client_id/tenant_id from existing O365 source
        # if not explicitly provided (same app registration for all mailboxes).
        if provider == "o365":
            _INHERITABLE = ("client_id", "tenant_id")
            missing = [f for f in _INHERITABLE if f not in kwargs]
            if missing:
                existing = sources.by_provider("o365")
                if existing:
                    donor = next(iter(existing.values()))
                    for f in missing:
                        if f in donor:
                            kwargs[f] = donor[f]
                    inherited = [f for f in missing if f in donor]
                    if inherited:
                        donor_prefix = next(iter(existing))
                        print(f"Inherited {', '.join(inherited)} from source {donor_prefix!r}.")

            if "client_id" not in kwargs:
                print(f"Error: client_id is required for the first O365 source.")
                print(f"Usage: ts4k src add {prefix} o365 client_id=<id> tenant_id=<tid>")
                return

            # For /me sources (no mailbox), resolve username from MSAL cache
            if "mailbox" not in kwargs:
                from ts4k.commands import _resolve_o365_username
                username = _resolve_o365_username(kwargs)
                if username:
                    kwargs["email"] = username

        entry = sources.add(prefix, provider=provider, **kwargs)
        print(f"Added source {prefix!r}:")
        for k, v in sorted(entry.items()):
            print(f"  {k}: {v}")

    elif action == "rm":
        prefix = args.prefix
        if sources.remove(prefix):
            print(f"Removed source {prefix!r}.")
        else:
            print(f"Source {prefix!r} not found.")

    elif action == "list":
        all_cfg = sources.list_all()
        if not all_cfg:
            print("No sources configured.")
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

    client_id = tenant_id = None
    for cfg in all_cfg.values():
        if cfg.get("provider", "").lower() == "o365":
            client_id = cfg.get("client_id")
            tenant_id = cfg.get("tenant_id", "common")
            if client_id:
                break

    if not client_id:
        print("Error: No O365 source with client_id configured.")
        sys.exit(1)

    adapter = O365Adapter(
        O365AdapterConfig(client_id=client_id, tenant_id=tenant_id),
        prefix="_discover",
    )

    print("Discovering O365 mailboxes for authenticated user...")
    try:
        async with adapter:
            result = await adapter.discover_mailboxes()
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    primary = result.get("primary", "")
    aliases = result.get("aliases", [])
    display_name = result.get("display_name", "")

    if not primary:
        print("  No mailbox found.")
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

    # Generate suggested commands for mailboxes not yet configured.
    next_suffix = ord("a")
    suggestions: list[str] = []
    for email in all_emails:
        if email.lower() in existing_mailboxes:
            continue
        while True:
            candidate = f"o{chr(next_suffix)}"
            next_suffix += 1
            if candidate not in used_prefixes:
                break
        suggestions.append(
            f"  ts4k src add {candidate} o365 mailbox={email}"
        )

    if suggestions:
        print("To add these mailboxes, run:")
        for s in suggestions:
            print(s)
        print()
        print("No extra sign-in needed — your existing auth covers all of them.")
    else:
        print("All discovered mailboxes are already configured.")


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
        print("Error: --source is required (or --resume to continue a job).")
        sys.exit(1)

    if getattr(args, "bg", False):
        if not source:
            print("Error: --source is required with --bg.")
            sys.exit(1)
        result = commands.spawn_background_preload(
            source=source,
            query=getattr(args, "query", None),
            contact=getattr(args, "contact", None),
            since=getattr(args, "since", None),
            pages=getattr(args, "max_pages", 100) or 100,
            batch_size=getattr(args, "page_size", 50) or 50,
            bodies=getattr(args, "bodies", False),
            throttle=getattr(args, "throttle", 0.2) or 0.2,
        )
        print(result)
        return

    result = await commands.preload(
        source=source or "",
        query=getattr(args, "query", None),
        contact=getattr(args, "contact", None),
        since=getattr(args, "since", None),
        pages=getattr(args, "max_pages", 100) or 100,
        batch_size=getattr(args, "page_size", 50) or 50,
        bodies=getattr(args, "bodies", False),
        resume=getattr(args, "resume", None),
        throttle=getattr(args, "throttle", 0.2) or 0.2,
    )
    print(result)


def _cmd_overview(args: argparse.Namespace) -> None:
    output = commands.overview(
        source=getattr(args, "source", None),
        contact=getattr(args, "contact", None),
        period=getattr(args, "period", None),
        fmt=getattr(args, "format", "pipe") or "pipe",
        top=getattr(args, "top", 10) or 10,
    )
    print(output)


def _cmd_cache(args: argparse.Namespace) -> None:
    action = getattr(args, "action", None)
    source = getattr(args, "source", None)
    stale = getattr(args, "stale", False)
    print(commands.manage_cache(action=action, source=source, stale=stale))


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
    """Handle the skill command — machine-readable output for Claude Code.

    Three tiers:
    - ``ts4k skill`` → tier 1 (core commands reference)
    - ``ts4k skill more`` → tier 2 (admin commands reference)
    - ``ts4k skill <cmd> [args]`` → route to command with pipe format
    """
    subcmd = getattr(args, "subcmd", None)
    if not subcmd:
        print(commands.skill_reference("basic"))
        return

    if subcmd == "more":
        print(commands.skill_reference("more"))
        return

    if subcmd == "setup":
        print(commands.llm_help())
        return

    argv = [subcmd] + (getattr(args, "skill_args", None) or [])
    # Force pipe format unless explicitly overridden
    if "-f" not in argv and "--format" not in argv:
        argv.extend(["-f", "pipe"])
    parser = _build_parser()
    sub_args = parser.parse_args(argv)

    if not hasattr(sub_args, "func") or sub_args.func is None:
        print(f"Unknown skill subcommand: {subcmd}. Run 'ts4k skill' for command reference.")
        sys.exit(1)

    await sub_args.func(sub_args)


def _cmd_auth(args: argparse.Namespace) -> None:
    """Handle the auth command — authenticate with a platform."""
    platform = getattr(args, "platform", None)

    if platform == "gmail":
        email = getattr(args, "email", None)
        if not email:
            print("Error: email is required.")
            sys.exit(1)

        from ts4k.auth.google import get_credentials

        check_only = getattr(args, "check", False)

        try:
            creds = get_credentials(email)
            if check_only:
                if creds.valid:
                    print(f"Credentials valid for {email}.")
                else:
                    print(f"Credentials exist but are not valid for {email}.")
                    sys.exit(1)
            else:
                print(f"Authenticated {email} successfully.")
        except FileNotFoundError as exc:
            if check_only:
                print(f"No credentials found for {email}.")
                print(f"Run: ts4k auth gmail {email}")
            else:
                print(f"Error: {exc}")
            sys.exit(1)
        except Exception as exc:
            print(f"Authentication failed: {exc}")
            sys.exit(1)
    elif platform == "o365":
        source_prefix = getattr(args, "source", None)

        from ts4k.state import sources as src_mod

        if source_prefix:
            cfg = src_mod.get(source_prefix)
            if not cfg or cfg.get("provider") != "o365":
                print(f"Error: source {source_prefix!r} is not an O365 source.")
                print("Check your sources: ts4k src list")
                sys.exit(1)
        else:
            o365_sources = src_mod.by_provider("o365")
            if not o365_sources:
                print("Error: no O365 sources configured.")
                print("Add one first: ts4k src add o o365 client_id=<id> tenant_id=<tid>")
                sys.exit(1)
            source_prefix = next(iter(o365_sources))
            cfg = o365_sources[source_prefix]
            if len(o365_sources) > 1:
                print(f"Multiple O365 sources found, using {source_prefix!r}.")
                print(f"Specify one explicitly: ts4k auth o365 <prefix>")

        client_id = cfg.get("client_id", "")
        tenant_id = cfg.get("tenant_id", "common") or "common"

        if not client_id:
            print(f"Error: source {source_prefix!r} is missing client_id.")
            print(f"Fix it: ts4k src add {source_prefix} o365 client_id=<id> tenant_id=<tid>")
            sys.exit(1)

        from ts4k.auth.microsoft import get_credentials as get_ms_credentials

        check_only = getattr(args, "check", False)

        try:
            creds = get_ms_credentials(client_id, tenant_id=tenant_id)
            if check_only:
                if "access_token" in creds:
                    print(f"Credentials valid for client {client_id}.")
                else:
                    print(f"Credentials exist but are not valid for client {client_id}.")
                    sys.exit(1)
            else:
                print(f"Authenticated client {client_id} successfully.")
        except Exception as exc:
            print(f"Authentication failed: {exc}")
            sys.exit(1)
    else:
        print("Usage: ts4k auth gmail <email>")
        print("       ts4k auth o365          (authenticates first O365 source)")
        print("       ts4k auth o365 <prefix> (authenticates a specific O365 source)")
        sys.exit(1)


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
    from ts4k import __version__

    parser = argparse.ArgumentParser(
        prog="ts4k",
        description="Token-efficient messaging gateway for LLM agents.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use .ts4k/ in current directory (created if missing)",
    )

    subparsers = parser.add_subparsers(dest="command", title="Commands", metavar="<command>")

    # --- whatsnew / wn / updates ---
    wn = subparsers.add_parser("updates", aliases=["whatsnew", "wn"], help="Show new messages (updates watermark)")
    wn.add_argument("--since", help="Time range: 2d, 6h, ISO timestamp, or Gmail query")
    wn.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
    wn.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all (default: all)")
    _add_common_args(wn)
    wn.set_defaults(func=_cmd_whatsnew)

    # --- get / g ---
    get = subparsers.add_parser("get", aliases=["g"], help="Read a single message")
    get.add_argument("id", help="Message ID (e.g. g:abc123 or w:3EB05C)")
    _add_common_args(get)
    get.set_defaults(func=_cmd_get)

    # --- thread / t ---
    th = subparsers.add_parser("thread", aliases=["t"], help="Read a thread or chat")
    th.add_argument("id", help="Thread/chat ID (e.g. g:abc123 or w:jid@s.whatsapp.net)")
    _add_common_args(th)
    th.set_defaults(func=_cmd_thread)

    # --- list / l ---
    ls = subparsers.add_parser("list", aliases=["l"], help="List messages matching a query")
    ls.add_argument("--query", "-q", help="Search query")
    ls.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
    ls.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all (default: all)")
    _add_common_args(ls)
    ls.set_defaults(func=_cmd_list)

    # --- sources / src ---
    sr = subparsers.add_parser("sources", aliases=["src"], help="Manage source config")
    sr_sub = sr.add_subparsers(dest="action")

    sr_add = sr_sub.add_parser(
        "add",
        help="Add a source",
        epilog=(
            "provider keys:\n"
            "  gmail:    email (required), mcp_url, transport\n"
            "  whatsapp: mcp_cwd (required), server_command\n"
            "  o365:     client_id (required), tenant_id, mailbox\n"
            "\n"
            "examples:\n"
            '  ts4k src add g gmail email=you@gmail.com\n'
            '  ts4k src add w whatsapp mcp_cwd=/path/to/server server_command="uv run python main.py"\n'
            "\n"
            "List fields (server_command) are auto-split on spaces.\n"
            "A bare email (user@example.com) is treated as email=user@example.com."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sr_add.add_argument("prefix", help="Source prefix (e.g. g, gn, w)")
    sr_add.add_argument("provider", help="Provider: gmail, o365, whatsapp")
    sr_add.add_argument("params", nargs="*", help="key=value pairs or bare email")

    sr_rm = sr_sub.add_parser("rm", help="Remove a source")
    sr_rm.add_argument("prefix", help="Source prefix to remove")

    sr_sub.add_parser("list", help="List all configured sources")

    sr_sub.add_parser("discover", help="Discover O365 mailboxes for authenticated user")

    sr.set_defaults(func=_cmd_sources)

    # --- contacts / c ---
    ct = subparsers.add_parser("contacts", aliases=["c"], help="Cross-platform contact identity map")
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
    fl = subparsers.add_parser("filter", aliases=["f"], help="Manage skip filters (off by default)")
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
    pl.add_argument("--since", help="Start date: 2d, 6h, or ISO timestamp")
    pl.add_argument("--max-pages", type=int, default=100, help="Max pages to fetch (default: 100)")
    pl.add_argument("--page-size", type=int, default=50, help="Messages per page (default: 50)")
    pl.add_argument("--bodies", action="store_true", help="Fetch full message bodies (slower)")
    pl.add_argument("--resume", metavar="JOB_ID", help="Resume an interrupted preload job")
    pl.add_argument("--throttle", type=float, default=0.2, help="Seconds between pages (default: 0.2)")
    pl.add_argument("--status", action="store_true", help="Show all preload jobs")
    pl.add_argument("--cancel", metavar="JOB_ID", help="Cancel a preload job")
    pl.add_argument("--bg", action="store_true", help="Run in background (returns job ID immediately)")
    pl.set_defaults(func=_cmd_preload)

    # --- overview / o ---
    ov = subparsers.add_parser("overview", aliases=["o"], help="Hierarchical overview of cached messages")
    ov.add_argument("--source", "-s", help="Drill down into a specific source prefix")
    ov.add_argument("--contact", "-c", help="Drill down into a specific contact")
    ov.add_argument("--period", "-p", help="Filter by period: YYYY, YYYY-QN, YYYY-MM, or YYYY-MM..YYYY-MM")
    ov.add_argument("--top", "-n", type=int, default=10, help="Number of top senders/threads (default: 10)")
    _add_common_args(ov)
    ov.set_defaults(func=_cmd_overview)

    # --- cache ---
    ca = subparsers.add_parser("cache", help="Manage message cache")
    ca_sub = ca.add_subparsers(dest="action")
    ca_sub.add_parser("stats", help="Show cache statistics")
    ca_clear = ca_sub.add_parser("clear", help="Clear cached messages")
    ca_clear.add_argument("--source", "-s", help="Clear only this source (e.g. g, o)")
    ca_clear.add_argument("--stale", action="store_true", help="Only clear stale (old schema) entries")
    ca.set_defaults(func=_cmd_cache)

    # --- status / st ---
    st = subparsers.add_parser("status", aliases=["st"], help="Operational status, stats, efficiency")
    st.add_argument("--live", "-L", action="store_true", help="Include live mailbox label/folder counts")
    st.add_argument("--source", "-s", help="Limit live stats to this source prefix")
    st.add_argument("-f", "--format", default="pipe", help="Format for mailbox section: pipe, json, xml")
    st.set_defaults(func=_cmd_status)

    # --- help / h ---
    hp = subparsers.add_parser("help", aliases=["h"], help="Quick reference for commands and flags")
    hp.add_argument("--llm", action="store_true", help="Agent-optimized reference (structured, context-aware)")
    hp.set_defaults(func=_cmd_help)

    # --- auth ---
    au = subparsers.add_parser("auth", help="Authenticate with a platform")
    au_sub = au.add_subparsers(dest="platform")

    au_gmail = au_sub.add_parser("gmail", help="Authenticate with Gmail (opens browser)")
    au_gmail.add_argument("email", help="Google email to authenticate")
    au_gmail.add_argument("--check", action="store_true", help="Verify credentials without re-auth")

    au_o365 = au_sub.add_parser("o365", help="Authenticate with Microsoft 365 (device code flow)")
    au_o365.add_argument("source", nargs="?", default=None, help="Source prefix to authenticate (e.g. 'o'). Uses first O365 source if omitted.")
    au_o365.add_argument("--check", action="store_true", help="Verify credentials without re-auth")

    au.set_defaults(func=_cmd_auth)

    # --- skill ---
    sk = subparsers.add_parser("skill", help="Compact reference for Claude Code skill integration")
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

    # --- Config directory resolution ---
    from pathlib import Path

    if args.local:
        local_dir = Path.cwd() / ".ts4k"
        local_dir.mkdir(exist_ok=True)
        gitignore = local_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("# Ignore all ts4k state (tokens, cache, etc.)\n*\n")
        state.set_config_dir(local_dir, reason="local-flag")
    else:
        resolved = state.get_config_dir()
        state.set_config_dir(resolved.path, reason=resolved.reason)

    if not hasattr(args, "func") or args.func is None:
        parser.print_help()
        sys.exit(1)

    if args.func in (_cmd_help, _cmd_contacts, _cmd_filter, _cmd_status, _cmd_sources, _cmd_cache, _cmd_overview, _cmd_auth):
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

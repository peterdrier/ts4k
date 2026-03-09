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


def _refs_path(key: str | None = None) -> "Path":
    """Path to the CLI refs file."""
    from pathlib import Path

    base = state.get_config_dir().path
    if key:
        return base / f"refs-{key}.json"
    return base / "refs.json"


def _new_ref_table() -> RefTable:
    """Create a fresh RefTable for CLI listing commands (last-listing-wins)."""
    return RefTable()



# ---------------------------------------------------------------------------
# Command handlers — thin wrappers around commands.*
# ---------------------------------------------------------------------------


async def _cmd_updates(args: argparse.Namespace) -> None:
    refs = _new_ref_table()
    result = await commands.updates(
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
    print("→ ts4k get N to read message N")


async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(args.key))  # load existing, accumulate
    result = await commands.whatsnew(
        key=args.key,
        source=getattr(args, "source", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        filter=getattr(args, "filter", False),
        ref_table=refs,
    )
    if result.error:
        print(result.error)
        return
    refs.save(_refs_path(args.key))  # save accumulated
    print(result.output)
    print(f"→ ts4k get -k {args.key} N to read message N")


async def _cmd_get(args: argparse.Namespace) -> None:
    msg_id = args.id
    key = getattr(args, "key", None)
    if msg_id.lstrip("#").isdigit():
        rt = RefTable()
        rt.load(_refs_path(key))
        resolved = rt.resolve(msg_id)
        if resolved is None:
            label = f"key '{key}'" if key else "global refs"
            print(f"Ref {msg_id} not found in {label}. Run 'whatsnew' or 'updates' first.")
            sys.exit(1)
        msg_id = resolved
    result = await commands.get_message(
        id=msg_id,
        fmt=getattr(args, "format", "pipe") or "pipe",
    )
    if result.error:
        print(result.error)
        sys.exit(1)
    print(result.output)


async def _cmd_thread(args: argparse.Namespace) -> None:
    tid = args.id
    key = getattr(args, "key", None)
    if tid.lstrip("#").isdigit():
        rt = RefTable()
        rt.load(_refs_path(key))
        resolved = rt.resolve(tid)
        if resolved is None:
            label = f"key '{key}'" if key else "global refs"
            print(f"Ref {tid} not found in {label}. Run 'whatsnew' or 'updates' first.")
            sys.exit(1)
        tid = resolved
    result = await commands.get_thread(
        tid=tid,
        fmt=getattr(args, "format", "pipe") or "pipe",
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
    print("→ ts4k get N to read message N")


def _cmd_help(args: argparse.Namespace) -> None:
    """Handle the help / h command — quick reference for commands and flags."""
    if getattr(args, "llm", False):
        print(commands.llm_help())
        return

    all_cfg = sources.list_all()

    print("ts4k — Token Saver 4000")
    print()
    print("Commands:")
    print("  updates [--since 2d] [--source S] [-n N]   Fetch messages by time range  [u]")
    print("  whatsnew KEY [--source S] [-n N]            Check new (keyed watermarks)  [wn]")
    print("  list [-q QUERY] [--source S] [-n N]         Search messages              [l]")
    print("  get [-k KEY] ID                             Read a message               [g]")
    print("  thread [-k KEY] TID                         Read a thread/chat           [t]")
    print("  overview [--source S] [--contact C]         Cache summary (drill-down)   [o]")
    print("  status                                      Health, stats, efficiency    [st]")
    print()
    print("  src list|add|rm                             Manage sources")
    print("  contacts link|unlink|find|list              Manage contacts              [c]")
    print("  filter show|add-*|rm-*|reset                Manage filters               [f]")
    print("  preload --source S [--query Q] [--bg]       Paginate history into cache")
    print("  cache stats|clear [--source S] [--stale]    Manage message cache")
    print("  auth gmail|o365                              Authenticate with a platform")
    print("  skill                                       Agent-oriented command reference")
    print()
    print("Refs:  listings assign numbers (1, 2, 3...) — use with get/thread")
    print("       whatsnew refs accumulate per key; use get -k KEY N to resolve")
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

    Subcommands:
    - ``ts4k skill`` → tier 1 (core commands reference)
    - ``ts4k skill more`` → tier 2 (admin commands reference)
    - ``ts4k skill setup`` → context-aware setup/troubleshooting
    - ``ts4k skill install [--project]`` → install Claude Code skill file
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

    if subcmd == "install":
        project = getattr(args, "project", False)
        _install_skill(project=project)
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


def _install_skill(project: bool = False) -> None:
    """Install the ts4k skill file for Claude Code."""
    from pathlib import Path

    if project:
        target_dir = Path(".claude/skills/ts")
    else:
        claude_dir = Path.home() / ".claude"
        if not claude_dir.is_dir():
            print("Error: ~/.claude/ not found. Is Claude Code installed?")
            sys.exit(1)
        target_dir = claude_dir / "skills" / "ts"

    target_file = target_dir / "SKILL.md"

    if target_file.exists():
        print(f"Warning: {target_file} already exists. Overwriting.")

    target_dir.mkdir(parents=True, exist_ok=True)
    target_file.write_text(commands.skill_template())
    print(f"Installed ts4k skill to {target_file}")


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

    # --- updates / u ---
    up = subparsers.add_parser(
        "updates", aliases=["u"],
        help="Fetch messages by time range (stateless)",
        description="Fetch messages within a time range without updating watermarks. Useful for ad-hoc searches and re-reading recent mail.",
        epilog=(
            "examples:\n"
            "  ts4k updates --since 2d          # last 2 days, all sources\n"
            "  ts4k u --since 6h -s g           # last 6 hours, Gmail only\n"
            "  ts4k u --since 2025-01-01 -n 50  # since date, up to 50 msgs\n"
            "  ts4k u --since 1w -f json         # last week, JSON output"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    up.add_argument("--since", help="Time range: 2d, 6h, ISO timestamp, or Gmail query")
    up.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
    up.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all (default: all)")
    _add_common_args(up)
    up.set_defaults(func=_cmd_updates)

    # --- whatsnew / wn ---
    wn = subparsers.add_parser(
        "whatsnew", aliases=["wn"],
        help="Check for new messages (keyed watermarks)",
        description="Show messages newer than the last watermark for a given key. Each call advances the watermark so the same messages are never shown twice. First call for a new key returns the latest batch.",
        epilog=(
            "examples:\n"
            "  ts4k whatsnew life               # new msgs for 'life' key\n"
            "  ts4k wn work -s g               # new Gmail msgs for 'work'\n"
            "  ts4k wn life -n 50              # up to 50 new messages\n"
            "  ts4k wn alerts -f json          # JSON output"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    wn.add_argument("key", help="Watermark key (e.g. life, peter)")
    wn.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
    wn.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all")
    _add_common_args(wn)
    wn.set_defaults(func=_cmd_whatsnew)

    # --- get / g ---
    get = subparsers.add_parser(
        "get", aliases=["g"],
        help="Read a single message",
        description="Retrieve the full content of a single message by native ID or ref number from a whatsnew result.",
        epilog=(
            "examples:\n"
            "  ts4k get g:18f3a2b1c4d5e6f7    # by native Gmail ID\n"
            "  ts4k g 7 -k life               # ref #7 from 'life' whatsnew\n"
            "  ts4k g w:1234@wa               # by native WhatsApp ID\n"
            "  ts4k g 3 -k work -f json       # ref #3, JSON output"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    get.add_argument("id", help="Message ID (e.g. g:abc123) or ref number (e.g. 7)")
    get.add_argument("--key", "-k", help="Whatsnew key for ref lookup (e.g. life)")
    _add_common_args(get)
    get.set_defaults(func=_cmd_get)

    # --- thread / t ---
    th = subparsers.add_parser(
        "thread", aliases=["t"],
        help="Read a thread or chat",
        description="Retrieve all messages in a thread or chat. Resolves the thread from any message ID or ref number within it.",
        epilog=(
            "examples:\n"
            "  ts4k thread g:18f3a2b1c4d5e6f7  # thread containing this msg\n"
            "  ts4k t 7 -k life                # thread for ref #7\n"
            "  ts4k t w:chat123 --tail 5        # last 5 msgs in WhatsApp chat"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    th.add_argument("id", help="Thread/chat ID (e.g. g:abc123) or ref number (e.g. 7)")
    th.add_argument("--key", "-k", help="Whatsnew key for ref lookup (e.g. life)")
    _add_common_args(th)
    th.set_defaults(func=_cmd_thread)

    # --- list / l ---
    ls = subparsers.add_parser(
        "list", aliases=["l"],
        help="List messages matching a query",
        description="Search for messages using provider-native query syntax. Gmail supports full Gmail search operators; other providers support basic text search.",
        epilog=(
            "examples:\n"
            "  ts4k list -q 'from:boss subject:urgent'  # Gmail search\n"
            "  ts4k l -q invoice -s g -n 10             # Gmail, 10 results\n"
            "  ts4k l -q 'after:2025/01/01' -f json     # date filter, JSON"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ls.add_argument("--query", "-q", help="Search query")
    ls.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
    ls.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all (default: all)")
    _add_common_args(ls)
    ls.set_defaults(func=_cmd_list)

    # --- sources / src ---
    sr = subparsers.add_parser(
        "sources", aliases=["src"],
        help="Manage source config",
        description="Manage messaging sources. Each source has a short prefix (g, w, o) used as a namespace for message IDs.",
        epilog=(
            "examples:\n"
            "  ts4k src list                    # show configured sources\n"
            "  ts4k src add g gmail you@gmail.com\n"
            "  ts4k src rm g                    # remove a source\n"
            "  ts4k src discover                # find O365 mailboxes"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sr_sub = sr.add_subparsers(dest="action")

    sr_add = sr_sub.add_parser(
        "add",
        help="Add a source",
        description="Register a new messaging source with a prefix and provider-specific parameters.",
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

    sr_rm = sr_sub.add_parser("rm", help="Remove a source",
        description="Remove a configured source by its prefix.")
    sr_rm.add_argument("prefix", help="Source prefix to remove")

    sr_sub.add_parser("list", help="List all configured sources",
        description="Show all configured sources with their prefixes, providers, and parameters.")

    sr_sub.add_parser("discover", help="Discover O365 mailboxes for authenticated user",
        description="Query Microsoft Graph to find available mailboxes for the authenticated O365 user.")

    sr.set_defaults(func=_cmd_sources)

    # --- contacts / c ---
    ct = subparsers.add_parser(
        "contacts", aliases=["c"],
        help="Cross-platform contact identity map",
        description="Map identities across platforms so the same person shows one alias regardless of source. Link Gmail addresses, WhatsApp numbers, and O365 accounts to a single contact name.",
        epilog=(
            "examples:\n"
            "  ts4k c link sarah g:sarah@gmail.com w:123@wa\n"
            "  ts4k c unlink sarah w:123@wa     # remove one identifier\n"
            "  ts4k c unlink sarah               # delete alias entirely\n"
            "  ts4k c find sarah                 # search by alias or ID\n"
            "  ts4k c list                       # show all contacts"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ct_sub = ct.add_subparsers(dest="action")

    ct_link = ct_sub.add_parser("link", help="Link identifiers to an alias",
        description="Associate one or more platform identifiers with a contact alias.")
    ct_link.add_argument("alias", help="Contact alias (e.g. sarah)")
    ct_link.add_argument("identifiers", nargs="+", help="Platform IDs (e.g. g:sarah@gmail.com w:123@wa)")

    ct_unlink = ct_sub.add_parser("unlink", help="Unlink identifiers or remove alias",
        description="Remove specific identifiers from a contact, or delete the alias entirely if no identifiers are given.")
    ct_unlink.add_argument("alias", help="Contact alias")
    ct_unlink.add_argument("identifiers", nargs="*", help="IDs to remove (omit to delete alias)")

    ct_sub.add_parser("list", help="List all contacts",
        description="Show all contact aliases and their linked identifiers.")

    ct_find = ct_sub.add_parser("find", help="Search contacts",
        description="Search contacts by alias name or platform identifier substring.")
    ct_find.add_argument("term", help="Search term (matches alias or identifier)")

    ct.set_defaults(func=_cmd_contacts)

    # --- filter / f ---
    fl = subparsers.add_parser(
        "filter", aliases=["f"],
        help="Manage skip filters (off by default)",
        description="Manage opt-in skip filters. Filters are off by default — add senders, domains, or patterns to skip. Use -F on messaging commands to apply filters.",
        epilog=(
            "examples:\n"
            "  ts4k filter add-sender noreply@spam.com\n"
            "  ts4k f add-domain newsletters.example.com\n"
            "  ts4k f add-pattern '^Out of office'\n"
            "  ts4k f skip-groups true          # skip group chats\n"
            "  ts4k f show                      # show current config\n"
            "  ts4k wn life -F                  # apply filters to whatsnew"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
    pl = subparsers.add_parser(
        "preload",
        help="Paginate through history into cache",
        description="Paginate through message history and store results in the local cache. Supports background execution, throttling, and resumable jobs.",
        epilog=(
            "examples:\n"
            "  ts4k preload -s g --since 30d             # last 30 days of Gmail\n"
            "  ts4k preload -s g --contact sarah --bg    # background job\n"
            "  ts4k preload --resume job_abc123           # resume interrupted\n"
            "  ts4k preload --status                      # show all jobs\n"
            "  ts4k preload --cancel job_abc123           # cancel a job"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
    ov = subparsers.add_parser(
        "overview", aliases=["o"],
        help="Hierarchical overview of cached messages",
        description="Show a hierarchical summary of cached messages with three drill-down levels: all sources, per-source, and per-contact. Optionally filter by time period.",
        epilog=(
            "examples:\n"
            "  ts4k overview                    # top-level summary\n"
            "  ts4k o -s g                      # drill into Gmail\n"
            "  ts4k o -c sarah                  # drill into contact\n"
            "  ts4k o -p 2025-Q1               # filter by quarter\n"
            "  ts4k o -s g -p 2025-01..2025-03  # Gmail, Jan-Mar 2025"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ov.add_argument("--source", "-s", help="Drill down into a specific source prefix")
    ov.add_argument("--contact", "-c", help="Drill down into a specific contact")
    ov.add_argument("--period", "-p", help="Filter by period: YYYY, YYYY-QN, YYYY-MM, or YYYY-MM..YYYY-MM")
    ov.add_argument("--top", "-n", type=int, default=10, help="Number of top senders/threads (default: 10)")
    _add_common_args(ov)
    ov.set_defaults(func=_cmd_overview)

    # --- cache ---
    ca = subparsers.add_parser(
        "cache",
        help="Manage message cache",
        description="Manage the local message cache. Cached messages are stored from preload jobs and previous fetches.",
        epilog=(
            "examples:\n"
            "  ts4k cache stats                 # show cache size/counts\n"
            "  ts4k cache clear                 # clear entire cache\n"
            "  ts4k cache clear -s g            # clear Gmail cache only\n"
            "  ts4k cache clear --stale         # clear old-schema entries"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ca_sub = ca.add_subparsers(dest="action")
    ca_sub.add_parser("stats", help="Show cache statistics")
    ca_clear = ca_sub.add_parser("clear", help="Clear cached messages")
    ca_clear.add_argument("--source", "-s", help="Clear only this source (e.g. g, o)")
    ca_clear.add_argument("--stale", action="store_true", help="Only clear stale (old schema) entries")
    ca.set_defaults(func=_cmd_cache)

    # --- status / st ---
    st = subparsers.add_parser(
        "status", aliases=["st"],
        help="Operational status, stats, efficiency",
        description="Show operational status including configured sources, watermark keys, filter state, and token efficiency stats. Use --live to query mailbox label/folder counts.",
        epilog=(
            "examples:\n"
            "  ts4k status                      # local status overview\n"
            "  ts4k st --live                   # include live mailbox counts\n"
            "  ts4k st --live -s g              # live stats for Gmail only\n"
            "  ts4k st --live -f json           # mailbox stats as JSON"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    st.add_argument("--live", "-L", action="store_true", help="Include live mailbox label/folder counts")
    st.add_argument("--source", "-s", help="Limit live stats to this source prefix")
    st.add_argument("-f", "--format", default="pipe", help="Format for mailbox section: pipe, json, xml")
    st.set_defaults(func=_cmd_status)

    # --- help / h ---
    hp = subparsers.add_parser(
        "help", aliases=["h"],
        help="Quick reference for commands and flags",
        description="Show a quick-reference summary of all commands and flags. Use --llm for a structured, agent-optimized version.",
    )
    hp.add_argument("--llm", action="store_true", help="Agent-optimized reference (structured, context-aware)")
    hp.set_defaults(func=_cmd_help)

    # --- auth ---
    au = subparsers.add_parser(
        "auth",
        help="Authenticate with a platform",
        description="Authenticate with a messaging platform. Gmail uses browser-based OAuth; O365 uses device code flow.",
        epilog=(
            "examples:\n"
            "  ts4k auth gmail you@gmail.com    # OAuth in browser\n"
            "  ts4k auth gmail you@gmail.com --check  # verify creds\n"
            "  ts4k auth o365                   # device code flow\n"
            "  ts4k auth o365 o --check         # verify O365 creds"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    au_sub = au.add_subparsers(dest="platform")

    au_gmail = au_sub.add_parser("gmail", help="Authenticate with Gmail (opens browser)")
    au_gmail.add_argument("email", help="Google email to authenticate")
    au_gmail.add_argument("--check", action="store_true", help="Verify credentials without re-auth")

    au_o365 = au_sub.add_parser("o365", help="Authenticate with Microsoft 365 (device code flow)")
    au_o365.add_argument("source", nargs="?", default=None, help="Source prefix to authenticate (e.g. 'o'). Uses first O365 source if omitted.")
    au_o365.add_argument("--check", action="store_true", help="Verify credentials without re-auth")

    au.set_defaults(func=_cmd_auth)

    # --- skill ---
    sk = subparsers.add_parser(
        "skill",
        help="Compact reference for Claude Code skill integration",
        description="Output a compact command reference for use by Claude Code skills. Agent-facing — not intended for direct human use.",
        epilog=(
            "subcommands:\n"
            "  ts4k skill                 # core command reference\n"
            "  ts4k skill more            # admin commands\n"
            "  ts4k skill setup           # context-aware setup/troubleshooting\n"
            "  ts4k skill install         # install skill to ~/.claude/skills/ts/\n"
            "  ts4k skill install --project  # install to .claude/skills/ts/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sk.add_argument("subcmd", nargs="?", help="Subcommand: install, more, setup, or a ts4k command")
    sk.add_argument("skill_args", nargs="*", help="Arguments for the subcommand")
    sk.add_argument("--project", action="store_true", help="Install to .claude/skills/ts/ (project-level)")
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

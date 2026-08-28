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
    TS4K_TIMEZONE          Display timezone for calendar times (IANA name).
                           Overrides "timezone" in settings.json; both
                           default to the machine's own zone.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ts4k import commands, state
from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig
from ts4k.state import sources
from ts4k.state.refs import RefTable


# Source config keys holding a secret rather than a pointer to one. `src list`
# runs constantly in agent context and in terminal scrollback, so the value
# itself never gets printed — `bridge_token_file` (a path) still does.
# `header` (#31 HTTP sources) commonly carries "Authorization: Bearer ..."
# or an API key, so it's redacted the same way as the other credentials.
_SECRET_SOURCE_KEYS = frozenset({"bridge_token", "token", "header"})


def _shown(key: str, value: object) -> object:
    """Value to print for a source config field — secrets never print.

    Every place that echoes a source config goes through here; `src add` and
    `src list` both display the same entries, so redacting only one of them
    just moves where the key leaks.
    """
    return "<redacted>" if key in _SECRET_SOURCE_KEYS else value


# ---------------------------------------------------------------------------
# Ref table helpers
# ---------------------------------------------------------------------------


def _refs_path(key: str | None = None) -> "Path":
    """Path to the CLI refs file."""

    base = state.get_config_dir().path
    if key:
        return base / f"refs-{key}.json"
    return base / "refs.json"


def _suggest_ref_table(ref: str, current_key: str | None, cmd: str = "get") -> str:
    """Check other ref tables for the ref and suggest the right command."""
    base = state.get_config_dir().path

    # If agent used a key, check global
    if current_key is not None:
        rt = RefTable()
        rt.load(_refs_path(None))
        if rt.resolve(ref) is not None:
            return f" Found in global refs — try: ts4k {cmd} {ref}"

    # Check keyed tables
    for path in sorted(base.glob("refs-*.json")):
        k = path.stem.removeprefix("refs-")
        if k == current_key:
            continue
        rt = RefTable()
        rt.load(path)
        if rt.resolve(ref) is not None:
            return f" Found in key '{k}' — try: ts4k {cmd} -k {k} {ref}"

    return " Run 'whatsnew' or 'list' first."




# ---------------------------------------------------------------------------
# Command handlers — thin wrappers around commands.*
# ---------------------------------------------------------------------------



async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(args.key))  # load existing, accumulate
    threads = getattr(args, "threads", False)
    result = await commands.whatsnew(
        key=args.key,
        source=getattr(args, "source", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        filter=getattr(args, "filter", False),
        ref_table=refs,
        threads=threads,
    )
    if result.error:
        print(result.error)
        return
    refs.save(_refs_path(args.key))  # save accumulated
    print(result.output)
    if threads:
        print(f"→ ts4k thread -k {args.key} N to read thread N")
    else:
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
            hint = _suggest_ref_table(msg_id, key)
            print(f"Ref {msg_id} not found in {label}.{hint}")
            sys.exit(1)
        msg_id = resolved
    if getattr(args, "media", False):
        result = await commands.get_media(id=msg_id)
        if result.error:
            print(result.error)
            sys.exit(1)
        print(result.output)
        return
    body_mode = getattr(args, "body_mode", None) or (
        "readable" if getattr(args, "readable", False) else "compact"
    )
    result = await commands.get_message(
        id=msg_id,
        fmt=getattr(args, "format", "pipe") or "pipe",
        body_mode=body_mode,
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
            hint = _suggest_ref_table(tid, key, cmd="thread")
            print(f"Ref {tid} not found in {label}.{hint}")
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
    key = getattr(args, "key", None)
    refs = RefTable()
    refs.load(_refs_path(key))
    threads = getattr(args, "threads", False)
    result = await commands.list_messages(
        source=getattr(args, "source", None),
        query=getattr(args, "query", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        filter=getattr(args, "filter", False),
        ref_table=refs,
        sender=getattr(args, "sender", None),
        domain=getattr(args, "domain", None),
        since=getattr(args, "since", None),
        threads=threads,
    )
    if result.error:
        print(result.error)
        return
    refs.save(_refs_path(key))
    print(result.output)
    cmd, noun = ("thread", "thread") if threads else ("get", "message")
    if key:
        print(f"→ ts4k {cmd} -k {key} N to read {noun} N")
    else:
        print(f"→ ts4k {cmd} N to read {noun} N")
    if result._continuation_hint:
        print(f"→ {result._continuation_hint}  (older messages)")


def _cmd_help(args: argparse.Namespace) -> None:
    """Handle the help / h command — quick reference for commands and flags."""
    if getattr(args, "llm", False):
        print(commands.llm_help())
        return

    all_cfg = sources.list_all()

    print("ts4k — Token Saver 4000")
    print()
    print("Commands:")
    print("  whatsnew KEY [--source S] [-n N]            Check new (keyed watermarks)  [wn]")
    print("  list [--since T] [-q Q] [--source S] [-n N] [--from/--domain] [--threads]  Search [l]")
    print("  get [-k KEY] ID                             Read a message               [g]")
    print("  thread [-k KEY] TID                         Read a thread/chat           [t]")
    print("  overview [--source S] [--contact C]         Cache summary (drill-down)   [o]")
    print("  status                                      Health, stats, efficiency    [st]")
    print()
    print("  src list|add|rm                             Manage sources")
    print("  contacts link|unlink|find|list|sync         Manage contacts              [c]")
    print("  filter show|add-*|rm-*|reset                Manage filters               [f]")
    print("  preload --source S [--query Q] [--bg]       Paginate history into cache")
    print("  cache stats|clear [--source S] [--stale]    Manage message cache")
    print("  auth [source|provider]                       Authenticate or validate tokens")
    print("  skill                                       Agent-oriented command reference")
    print()
    print("Calendar:")
    print("  cal [today|tomorrow|week]                   Calendar events (default: today)")
    print("  cal next [N]                                Next N events (default 10)")
    print("  cal range --from DATE --to DATE             Events in date range")
    print("  cal event REF                               Full event detail")
    print("  cal create -s S --title T --start DT --end DT  Create event")
    print("  cal update REF [--title T] [--start/--end]  Update event")
    print("  cal rsvp REF --status accepted|declined|tentative")
    print("  cal setup                                   Discover & add calendar sources")
    print()
    print("Refs:  listings assign numbers (1, 2, 3...) — use with get/thread/event/manage")
    print("       whatsnew refs accumulate per key; use get -k KEY N to resolve")
    print("       ts4k sources  shows configured source prefixes")
    print()
    print("Threads: list/whatsnew --threads  one row per thread; refs resolve to threads")
    print("         manage ACTION REF --thread  act on every message in the thread (Gmail)")

    if not all_cfg:
        print()
        print("Quick setup:")
        print("  1. ts4k src add g gmail email=you@gmail.com")
        print("  2. ts4k auth g")
        print("  3. ts4k list --since 2d")


def _cmd_contacts(args: argparse.Namespace) -> None:
    output = commands.manage_contacts(
        action=getattr(args, "action", None),
        alias=getattr(args, "alias", None),
        identifiers=getattr(args, "identifiers", None),
        term=getattr(args, "term", None),
    )
    print(output)


async def _cmd_contacts_sync(args: argparse.Namespace) -> None:
    output = await commands.sync_contacts(
        source=getattr(args, "source", None),
        apply=getattr(args, "apply", False),
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


def _local_timezone() -> str:
    """Best-effort IANA name for the system's local timezone; falls back to UTC."""
    import zoneinfo
    tz = os.environ.get("TZ")
    if tz:
        try:
            zoneinfo.ZoneInfo(tz)
            return tz
        except Exception:
            pass
    try:
        resolved = Path("/etc/localtime").resolve()
        parts = resolved.parts
        if "zoneinfo" in parts:
            name = "/".join(parts[parts.index("zoneinfo") + 1:])
            zoneinfo.ZoneInfo(name)
            return name
    except Exception:
        pass
    return "UTC"


def _prompt_password(prompt: str) -> str:
    """Prompt for a secret, echoing '*' per character; getpass fallback off-TTY."""
    if not sys.stdin.isatty():
        import getpass
        return getpass.getpass(prompt)
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    print(prompt, end="", flush=True)
    chars: list[str] = []
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                break
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch in ("\x7f", "\b"):
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            chars.append(ch)
            sys.stdout.write("*")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print()
    return "".join(chars)


def _ensure_apple_password(
    email: str, *, is_icloud: bool, server_url: str, username: str | None = None,
    store_server_url: bool = True, config_dir: Path | None = None,
    credential_key: str | None = None,
) -> str | None:
    """Make sure an app-specific password is stored for *email*.

    Returns ``"existing"`` when one was already on disk, ``"saved"`` when
    the user entered a new one, or ``None`` if the prompt was aborted.

    CalDAV and CardDAV share one credential file because Apple issues one
    app-specific password per Apple ID, not per service.  Its
    ``server_url`` is read back by the CalDAV adapter, so an iCloud
    contacts setup stores the CalDAV sibling endpoint, not the contacts
    host — the CardDAV adapter takes its base URL from the source config.
    Generic (non-iCloud) CardDAV setups pass ``store_server_url=False`` for
    the same reason: the CardDAV adapter never reads the shared file's
    ``server_url``, so writing the CardDAV endpoint there would only risk
    poisoning a CalDAV source that later reuses the same email.

    ``credential_key`` overrides the credential file's directory name
    (default: *email*).  Generic CardDAV setups pass an endpoint-scoped key
    (``<email>#carddav#<hash>``, see ``carddav_credential_key``) so they
    neither silently reuse a CalDAV credential for the same email — which
    may be a different password for a different service — nor collide
    with another generic CardDAV server that happens to share the same
    email.
    """
    from ts4k.auth.caldav import ICLOUD_CALDAV_URL, load_credentials, save_credentials

    key = credential_key or email
    if load_credentials(key, config_dir) is not None:
        return "existing"

    print("An app-specific password is required "
          "(https://account.apple.com → Sign-In and Security → "
          "App-Specific Passwords; needs 2FA).")
    pw = None
    for _attempt in range(3):
        raw = _prompt_password(f"App-specific password for {email}: ")
        # Apple's app-specific passwords are pasted as "xxxx xxxx xxxx xxxx"
        # and stripped to "xxxx-xxxx-xxxx-xxxx"; a generic server's password
        # has no such format and must be stored exactly as entered.
        stripped = "".join(raw.split())
        looks_apple = bool(re.fullmatch(r"[a-z]{4}-[a-z]{4}-[a-z]{4}-[a-z]{4}", stripped))
        if is_icloud and not looks_apple and re.fullmatch(r"[a-z]{16}", stripped):
            # Apple's account page also renders the password space-grouped
            # ("xxxx xxxx xxxx xxxx"); stripping whitespace above collapses
            # that to 16 contiguous letters instead of the hyphenated form
            # — re-hyphenate so it passes the format check below and is
            # stored the same way as a hyphenated paste.
            stripped = "-".join(stripped[i:i + 4] for i in range(0, 16, 4))
            looks_apple = True
        # Apple-format normalization applies ONLY to iCloud — a generic
        # server's password may legitimately be 16 lowercase letters (or
        # Apple-shaped) and must be stored byte-for-byte as entered
        candidate = stripped if is_icloud else raw
        if not candidate.strip():
            print("No password entered — aborting.")
            return None
        if is_icloud and not looks_apple:
            print("That doesn't look like an Apple app-specific password "
                  f"(expected xxxx-xxxx-xxxx-xxxx, got {len(stripped)} characters) "
                  "— try again.")
            continue
        pw = candidate
        break
    if pw is None:
        print("Too many failed attempts — aborting.")
        return None

    save_credentials(
        key, username=username or email, app_password=pw,
        server_url=ICLOUD_CALDAV_URL if is_icloud
        else (server_url if store_server_url else ""),
        config_dir=config_dir,
    )
    print(f"Saved credentials for {email}.")
    return "saved"


def _cmd_sources(args: argparse.Namespace) -> None:
    """Handle the src command — manage source config."""
    action = getattr(args, "action", None)

    if action == "add":
        prefix = args.prefix
        provider = args.provider.lower()
        kwargs: dict[str, Any] = {}
        # Fields that must be stored as lists (space-split from CLI string)
        _LIST_FIELDS = {"server_command"}
        # Fields that accumulate across repeated key=value occurrences
        # instead of the last one winning (http source auth headers, #31).
        _APPEND_FIELDS = {"header"}
        for kv in (args.params or []):
            if "=" in kv:
                k, v = kv.split("=", 1)
                k, v = k.strip(), v.strip()
                if k in _LIST_FIELDS:
                    kwargs[k] = v.split()
                elif k in _APPEND_FIELDS:
                    kwargs.setdefault(k, []).append(v)
                else:
                    kwargs[k] = v
            elif "@" in kv:
                # Bare email address — treat as email=value
                kwargs["email"] = kv.strip()

        # Apple/iCloud preset → generic caldav provider
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL
        _CALDAV_ALIASES = {"apple": ICLOUD_CALDAV_URL, "icloud": ICLOUD_CALDAV_URL,
                           "apple-calendar": ICLOUD_CALDAV_URL}
        if provider in _CALDAV_ALIASES:
            kwargs.setdefault("server_url", _CALDAV_ALIASES[provider])
            provider = "caldav"

        if provider == "caldav":
            email = kwargs.get("email")
            if not email:
                print("Error: email is required for CalDAV sources.")
                print(f"Usage: ts4k src add {prefix} apple email=you@icloud.com")
                return
            kwargs.setdefault("server_url", ICLOUD_CALDAV_URL)
            config_dir_path = Path(kwargs["config_dir"]) if kwargs.get("config_dir") else None

            stored = _ensure_apple_password(
                email,
                is_icloud=kwargs["server_url"] == ICLOUD_CALDAV_URL,
                server_url=kwargs["server_url"],
                username=kwargs.get("username"),
                config_dir=config_dir_path,
            )
            if stored is None:
                return
            fresh = stored == "saved"

            tz_default = kwargs.get("timezone") or _local_timezone()

            if "calendar_id" not in kwargs:
                print(f"Fetching calendars for {email}...")
                try:
                    cals = asyncio.run(
                        commands.cal_list_caldav_calendars(email, config_dir_path)
                    )
                except Exception as e:
                    print(f"Error: could not list calendars — {e}")
                    if fresh:
                        from ts4k.auth.caldav import credentials_path
                        credentials_path(email, config_dir_path).unlink(missing_ok=True)
                        print("Could not connect with that password — discarded the "
                              "saved credentials; run the command again to retry.")
                    return
                if not cals:
                    print("No calendars found.")
                    return
                for i, cal in enumerate(cals, 1):
                    print(f"  {i}. {cal['summary']}")
                choice = input("Which calendars? (comma-separated, or 'all'): ").strip()
                if choice.lower() == "all":
                    selected = cals
                else:
                    indices = [int(i.strip()) - 1
                               for i in choice.split(",") if i.strip().isdigit()]
                    selected = [cals[i] for i in indices if 0 <= i < len(cals)]
                if not selected:
                    print("No calendars selected.")
                    return
                all_sources = sources.list_all()
                for n, cal in enumerate(selected):
                    if n == 0 and prefix not in all_sources:
                        pfx = prefix
                    else:
                        suggested = _suggest_cal_prefix(cal["summary"], all_sources,
                                                        provider="caldav")
                        pfx = input(f"Prefix for '{cal['summary']}'? [{suggested}]: ").strip() or suggested
                    if pfx in all_sources:
                        print(f"  Prefix '{pfx}' already in use — skipping.")
                        continue
                    add_kwargs: dict[str, Any] = dict(
                        provider="caldav", email=email,
                        server_url=kwargs["server_url"],
                        calendar_id=cal["id"], calendar_name=cal["summary"],
                        timezone=tz_default, level="readonly",
                    )
                    if kwargs.get("config_dir"):
                        add_kwargs["config_dir"] = kwargs["config_dir"]
                    sources.add(pfx, **add_kwargs)
                    all_sources[pfx] = {}
                    print(f"  Added '{cal['summary']}' as '{pfx}' (readonly)")
                return
            # calendar_id given explicitly → fall through to generic sources.add
            kwargs.setdefault("timezone", tz_default)

        # Apple/iCloud contacts preset → generic carddav provider
        from ts4k.auth.caldav import ICLOUD_CARDDAV_URL, is_icloud_carddav_url
        if provider in ("apple-contacts", "icloud-contacts"):
            kwargs.setdefault("server_url", ICLOUD_CARDDAV_URL)
            provider = "carddav"

        if provider == "carddav":
            email = kwargs.get("email")
            if not email:
                print("Error: email is required for CardDAV sources.")
                print(f"Usage: ts4k src add {prefix} apple-contacts email=you@icloud.com")
                return
            kwargs.setdefault("server_url", ICLOUD_CARDDAV_URL)
            if urlsplit(kwargs["server_url"]).scheme != "https":
                print(f"Error: {kwargs['server_url']} is not HTTPS — CardDAV requires "
                      f"HTTPS, since credentials would otherwise be sent in cleartext. "
                      f"Use an https:// URL for server_url.")
                return

            from ts4k.adapters.carddav import carddav_credential_key
            is_icloud_contacts = is_icloud_carddav_url(kwargs["server_url"])
            stored = _ensure_apple_password(
                email,
                is_icloud=is_icloud_contacts,
                server_url=kwargs["server_url"],
                username=kwargs.get("username"),
                store_server_url=False,
                config_dir=Path(kwargs["config_dir"]) if kwargs.get("config_dir") else None,
                credential_key=carddav_credential_key(email, kwargs["server_url"]),
            )
            if stored is None:
                return
            if stored == "existing":
                print(f"Reusing the app-specific password already stored for {email}.")
            # Falls through to generic sources.add — contacts are imported by
            # `ts4k contacts sync`, not by any message command.

        if provider == "github":
            from ts4k.adapters.github import resolve_token
            if not resolve_token(kwargs.get("token"), kwargs.get("token_file")):
                print("Error: a GitHub personal access token is required.")
                print(f"Usage: ts4k src add {prefix} github token_file=<path>")
                print("       (or token=<pat>, or set GITHUB_TOKEN)")
                return

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
                print("Error: client_id is required for the first O365 source.")
                print(f"Usage: ts4k src add {prefix} o365 client_id=<id> tenant_id=<tid>")
                return

            # For /me sources (no mailbox), resolve username from MSAL cache
            if "mailbox" not in kwargs:
                from ts4k.commands import _resolve_o365_username
                username = _resolve_o365_username(kwargs)
                if username:
                    kwargs["email"] = username

        if provider == "http":
            if "url" not in kwargs:
                print("Error: url is required for HTTP sources.")
                print(f'Usage: ts4k src add {prefix} http url=<url> header="Name: value"')
                return

        existed = prefix in sources.list_all()
        entry = sources.add(prefix, provider=provider, **kwargs)
        verb = "Updated" if existed else "Added"
        print(f"{verb} source {prefix!r}:")
        for k, v in sorted(entry.items()):
            print(f"  {k}: {_shown(k, v)}")

    elif action == "rm":
        prefix = args.prefix
        if sources.remove(prefix):
            print(f"Removed source {prefix!r}.")
        else:
            print(f"Source {prefix!r} not found.")

    elif action == "note":
        prefix = args.prefix
        text = " ".join(args.text or []).strip()
        updated = sources.set_note(prefix, text)
        if updated is None:
            print(f"Source {prefix!r} not found.")
        elif text:
            print(f"Set note for {prefix!r}: {text}")
        else:
            print(f"Cleared note for {prefix!r}.")

    elif action == "list":
        all_cfg = sources.list_all()
        if not all_cfg:
            print("No sources configured.")
            print("Add one:  ts4k src add g gmail email=you@gmail.com")
            return
        headers_by_source = commands.cached_headers_by_source()
        for prefix, cfg in sorted(all_cfg.items()):
            provider = cfg.get("provider", "?")
            detail = cfg.get("email") or cfg.get("mailbox") or cfg.get("mcp_cwd") or ""
            level = cfg.get("level", "readonly")
            print(f"  {prefix}: {provider} ({detail}) [{level}]")
            act = commands.source_activity(
                prefix, provider, headers=headers_by_source.get(prefix, [])
            )
            if act["tag"] == "n/a":
                print("    activity: n/a (not cached locally)")
            elif act["tag"] == "empty":
                print("    activity: empty (no cached messages yet)")
            else:
                newest = act["newest"][:10] if act["newest"] else "unknown"
                print(f"    activity: {act['tag']} — {act['count']} cached, newest {newest}")
            note = cfg.get("note")
            if note:
                print(f"    note: {note}")
            for k, v in sorted(cfg.items()):
                # "note" is rendered above as its own line; secrets never print.
                if k not in ("provider", "email", "mailbox", "mcp_cwd", "note"):
                    print(f"    {k}: {_shown(k, v)}")

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


async def _cmd_manage_async(args: argparse.Namespace) -> None:
    key = getattr(args, "key", None)
    rt = RefTable()
    rt.load(_refs_path(key))
    result = await commands.manage_message(
        action=args.action,
        msg_id=args.id,
        label=getattr(args, "label", None),
        folder=getattr(args, "folder", None),
        dry_run=getattr(args, "dry_run", False),
        ref_table=rt,
        thread=getattr(args, "thread", False),
    )
    print(result)


async def _cmd_draft_async(args: argparse.Namespace) -> None:
    action = getattr(args, "action", None)
    if action != "create":
        print("Usage: ts4k draft create --source S --to ADDR --subject SUBJ --body TEXT")
        return
    key = getattr(args, "key", None)
    rt = RefTable()
    rt.load(_refs_path(key))
    result = await commands.create_draft(
        source=args.source,
        to=args.to,
        subject=args.subject,
        body=args.body,
        reply_to=getattr(args, "reply_to", None),
        ref_table=rt,
    )
    print(result)


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


async def _cmd_cal(args: argparse.Namespace) -> None:
    """Default: show today's events."""
    return await _cmd_cal_today(args)


async def _cmd_cal_today(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_today(
        source=getattr(args, "source", None),
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_tomorrow(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_tomorrow(
        source=getattr(args, "source", None),
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_week(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_week(
        source=getattr(args, "source", None),
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_next(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_next(
        source=getattr(args, "source", None),
        count=args.count or args.n or 10,
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_range(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_range(
        source=getattr(args, "source", None),
        from_date=args.from_date, to_date=args.to_date,
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_event(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    output = await commands.cal_event(
        ref_or_id=args.ref,
        source=getattr(args, "source", None),
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    print(output)


async def _cmd_cal_create(args: argparse.Namespace) -> None:
    attendees = [e.strip() for e in args.attendees.split(",")] if args.attendees else None
    output = await commands.cal_create(
        source=args.source, title=args.title, start=args.start, end=args.end,
        description=args.description, location=args.location,
        attendees=attendees,
    )
    print(output)


async def _cmd_cal_update(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    fields = {}
    for f in ("title", "start", "end", "description", "location"):
        val = getattr(args, f, None)
        if val is not None:
            fields[f] = val
    output = await commands.cal_update(
        ref_or_id=args.ref,
        source=getattr(args, "source", None),
        ref_table=refs, **fields,
    )
    print(output)


async def _cmd_cal_rsvp(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    output = await commands.cal_rsvp(
        ref_or_id=args.ref,
        source=getattr(args, "source", None),
        status=args.status, ref_table=refs,
    )
    print(output)


async def _cmd_cal_setup(args: argparse.Namespace) -> None:
    """Interactive calendar setup wizard."""
    from ts4k.state import sources as src_mod

    all_sources = src_mod.list_all()

    # Find Google accounts from gmail sources
    google_emails = {}
    for pfx, cfg in all_sources.items():
        if cfg.get("provider") == "gmail":
            email = cfg.get("email")
            if email:
                google_emails[email] = pfx

    # Find O365 accounts from o365 sources (skip shared-mailbox sources)
    o365_accounts = {}
    for pfx, cfg in all_sources.items():
        if cfg.get("provider") == "o365":
            if cfg.get("mailbox"):
                continue  # Shared mailbox — O365CalAdapter only supports /me
            email = cfg.get("email") or ""
            if not email:
                # Resolve from MSAL token cache
                from ts4k.commands import _resolve_o365_username
                email = _resolve_o365_username(cfg) or ""
            client_id = cfg.get("client_id", "")
            tenant_id = cfg.get("tenant_id", "common")
            if client_id and email:
                o365_accounts[email] = (pfx, client_id, tenant_id)

    if not google_emails and not o365_accounts:
        print("No Gmail or O365 sources found. Add a source first.")
        return

    # Collect all available calendars
    all_cals = []
    for email, gmail_prefix in google_emails.items():
        print(f"\nFound Google account: {email} (from source '{gmail_prefix}')")
        print(f"Fetching calendars for {email}...")
        try:
            cals = await commands.cal_list_calendars(email)
        except Exception as e:
            print(f"  Error: {e}")
            continue
        for cal in cals:
            # Skip already-configured calendars
            already = any(
                c.get("provider") == "gcal" and c.get("email") == email and c.get("calendar_id") == cal["id"]
                for c in all_sources.values()
            )
            if already:
                print(f"  (skipped: {cal['summary']} — already configured)")
                continue
            all_cals.append({"email": email, **cal})
            print(f"  {len(all_cals)}. {cal['summary']}" + (" (primary)" if cal.get("primary") else ""))

    for email, (o365_prefix, client_id, tenant_id) in o365_accounts.items():
        email_display = email
        print(f"\nFound O365 account: {email_display}")
        print("Fetching calendars...")
        try:
            cals = await commands.cal_list_o365_calendars(email, client_id, tenant_id)
        except Exception as e:
            print(f"  Error: {e}")
            continue
        for cal in cals:
            already = any(
                c.get("provider") == "o365cal" and c.get("client_id") == client_id and c.get("calendar_id") == cal["id"]
                for c in all_sources.values()
            )
            if already:
                print(f"  (skipped: {cal['summary']} — already configured)")
                continue
            all_cals.append({"email": email, "client_id": client_id, "tenant_id": tenant_id, "provider": "o365cal", **cal})
            print(f"  {len(all_cals)}. {cal['summary']}" + (" (primary)" if cal.get("primary") else "") + " [O365]")

    if not all_cals:
        print("\nNo new calendars to add.")
        return

    # Selection
    choice = input("\nWhich calendars? (comma-separated, or 'all'): ").strip()
    if choice.lower() == "all":
        selected = all_cals
    else:
        indices = [int(i.strip()) - 1 for i in choice.split(",") if i.strip().isdigit()]
        selected = [all_cals[i] for i in indices if 0 <= i < len(all_cals)]

    if not selected:
        print("No calendars selected.")
        return

    # Assign prefixes and add
    for cal in selected:
        suggested = _suggest_cal_prefix(cal["summary"], all_sources, provider=cal.get("provider", "gcal"))
        prefix = input(f"Prefix for '{cal['summary']}'? [{suggested}]: ").strip() or suggested

        if prefix in all_sources:
            print(f"  Prefix '{prefix}' already in use — skipping.")
            continue

        provider = cal.get("provider", "gcal")
        if provider == "o365cal":
            src_mod.add(
                prefix,
                provider="o365cal",
                email=cal["email"],
                client_id=cal["client_id"],
                tenant_id=cal["tenant_id"],
                calendar_id=cal["id"],
                calendar_name=cal["summary"],
                timezone=cal.get("timezone", "UTC"),
                level="readonly",
            )
        else:
            src_mod.add(
                prefix,
                provider="gcal",
                email=cal["email"],
                calendar_id=cal["id"],
                calendar_name=cal["summary"],
                timezone=cal.get("timezone", "UTC"),
                level="readonly",
            )
        all_sources[prefix] = {}  # Track for collision detection
        print(f"  Added '{cal['summary']}' as '{prefix}' (readonly)")

    print(f"\nAdded {len(selected)} calendar source(s).")


def _suggest_cal_prefix(name: str, existing: dict, provider: str = "gcal") -> str:
    """Suggest a short prefix for a calendar name."""
    base = {"o365cal": "oc", "caldav": "cc"}.get(provider, "gc")
    # Use first letter of each word after base
    words = name.lower().replace("@", " ").replace(".", " ").split()
    if words and words[0] not in ("primary", "my"):
        suffix = words[0][:1]
        candidate = f"{base}{suffix}"
    else:
        candidate = base

    # Avoid collisions
    if candidate not in existing:
        return candidate
    for i in range(2, 10):
        if f"{candidate}{i}" not in existing:
            return f"{candidate}{i}"
    return candidate


def _cmd_auth(args: argparse.Namespace) -> None:
    """Handle the unified auth command — authenticate or validate tokens."""
    from ts4k.state import sources as src_mod

    target = getattr(args, "target", None)
    check_only = getattr(args, "check", False)
    no_calendar = getattr(args, "no_calendar", False)

    all_sources = src_mod.list_all()

    # Resolve target -> list of (prefix, cfg) pairs to process
    targets: list[tuple[str, dict]] = []
    if target:
        # 1. Try as source prefix
        cfg = src_mod.get(target)
        if cfg:
            targets = [(target, cfg)]
        else:
            # 2. Try as provider name
            by_prov = src_mod.by_provider(target)
            if by_prov:
                targets = list(by_prov.items())
            else:
                print(f"Error: '{target}' is not a known source prefix or provider.")
                print(f"Sources: {', '.join(all_sources.keys()) or '(none)'}")
                print("Providers: gmail, o365 (gcal/o365cal share auth with gmail/o365)")
                sys.exit(1)
    elif check_only:
        # --check with no target -> check all
        targets = list(all_sources.items())
    else:
        # No target, no --check -> show help
        print("Usage: ts4k auth [target] [--check] [--no-calendar]")
        print()
        print("Target resolution:")
        print("  1. Source prefix first (g, gn, o, oc) — auths that specific source")
        print("  2. Provider name (gmail, o365) — auths all sources of that provider")
        print("  3. Omitted + --check — validates all sources")
        print("  4. Omitted without --check — shows this help")
        print()
        print("Examples:")
        print("  ts4k auth g                  Auth source 'g' (resolves email from config)")
        print("  ts4k auth gmail              Auth all Gmail sources")
        print("  ts4k auth o                  Auth source 'o' (O365, device code flow)")
        print("  ts4k auth --check            Validate all sources, no re-auth")
        print("  ts4k auth g --check          Validate just source 'g'")
        sys.exit(0)

    if not targets:
        print("No sources configured. Add one first: ts4k src add <prefix> <provider> ...")
        sys.exit(1)

    if check_only:
        _auth_check(targets)
    else:
        _auth_interactive(targets, no_calendar)


def _auth_check(targets: list[tuple[str, dict]]) -> None:
    """Validate tokens for one or more sources — no interactive flows."""
    from ts4k.commands import check_token_health

    any_bad = False
    for prefix, cfg in targets:
        health = check_token_health(prefix, cfg)
        provider = cfg.get("provider", "?")
        detail = cfg.get("email") or cfg.get("mailbox") or ""
        suffix = ""
        if health.status == "auth":
            reason = f" — {health.detail}" if health.detail else ""
            suffix = f"{reason} — ts4k auth {prefix}"
            any_bad = True
        elif health.status == "error":
            suffix = f" — {health.detail}"
            any_bad = True
        print(f"  {prefix:<4}{provider:<10}{detail:<30}[{health.status}]{suffix}")

    if any_bad:
        sys.exit(1)


def _auth_interactive(targets: list[tuple[str, dict]], no_calendar: bool) -> None:
    """Run interactive auth for one or more sources.

    Processes all targets even if some fail — reports errors inline
    and exits 1 at the end if any source failed.
    """
    any_failed = False
    for prefix, cfg in targets:
        provider = cfg.get("provider", "").lower()

        try:
            if provider in ("gmail", "gcal"):
                _auth_google(prefix, cfg, no_calendar)
            elif provider in ("o365", "o365cal"):
                _auth_o365(prefix, cfg, no_calendar)
            elif provider == "whatsapp":
                _auth_whatsapp(prefix, cfg)
            elif provider in ("caldav", "carddav"):
                from ts4k.auth.caldav import (
                    ICLOUD_CARDDAV_URL,
                    credentials_path,
                    is_icloud_carddav_url,
                )
                email = cfg.get("email", "<your-apple-id>")
                server_url = cfg.get("server_url", ICLOUD_CARDDAV_URL)
                is_generic_carddav = provider == "carddav" and not is_icloud_carddav_url(server_url)
                print(f"  {prefix}: {provider} — no OAuth; uses an app-specific password")
                print("        Generate one at https://account.apple.com "
                      "(Sign-In and Security → App-Specific Passwords),")
                if is_generic_carddav:
                    from ts4k.adapters.carddav import carddav_credential_key
                    key = carddav_credential_key(email, server_url)
                    print(f"        then store it with: ts4k src add {prefix} carddav "
                          f"email={email} server_url={server_url}")
                else:
                    alias = "apple-contacts" if provider == "carddav" else "apple"
                    key = email
                    print(f"        then store it with: ts4k src add {prefix} {alias} email={email}")
                # src add only prompts when no credential is stored, so a revoked
                # password has to be removed first or the re-run is a no-op.
                config_dir = Path(cfg["config_dir"]) if cfg.get("config_dir") else None
                print(f"        Replacing a revoked password? delete "
                      f"{credentials_path(key, config_dir)} first.")
            else:
                print(f"  {prefix}: unknown provider '{provider}' — skipping")
        except SystemExit:
            any_failed = True
        except Exception as exc:
            print(f"  {prefix}: error — {exc}")
            any_failed = True

    if any_failed:
        sys.exit(1)


def _auth_whatsapp(prefix: str, cfg: dict) -> None:
    """Report how a WhatsApp source authenticates — there is nothing to run.

    The WhatsApp session lives in the bridge (paired by QR, once). What ts4k
    needs is the bridge's HMAC key, which the operator pastes in rather than
    obtains through a flow.
    """
    from ts4k.adapters import wa_bridge_auth

    if (cfg.get("transport") or "http").lower() == "stdio":
        print(f"  {prefix}: whatsapp (stdio) — session-based, no auth needed")
        return
    if wa_bridge_auth.resolve_bridge_token(cfg.get("bridge_token"), cfg.get("bridge_token_file")):
        print(f"  {prefix}: whatsapp — bridge key configured, nothing to authorize")
        return
    print(f"  {prefix}: whatsapp — no bridge key configured")
    print("        The bridge mints one at <whatsapp-bridge>/store/api_token on first run.")
    print(f"        ts4k src add {prefix} whatsapp "
          "bridge_token_file=/path/to/whatsapp-bridge/store/api_token")


def _auth_google(prefix: str, cfg: dict, no_calendar: bool) -> None:
    """Authenticate a Google source (gmail or gcal)."""
    from ts4k.auth.google import get_credentials, union_scopes_for_email
    from ts4k.core.levels import scopes_for, parse_level

    email = cfg.get("email", "")
    if not email:
        print(f"Error: source '{prefix}' has no email configured.")
        sys.exit(1)

    # Build scopes: union of ALL Google sources for this email.
    # Gmail and gcal share one token per email, so authing any source
    # must request scopes for all sources to avoid overwriting.
    source_level = cfg.get("level")
    provider = cfg.get("provider", "gmail")
    scopes = scopes_for(provider, parse_level(source_level)) or []
    scopes.extend(
        s for s in union_scopes_for_email(email, include_calendar_readonly=not no_calendar)
        if s not in scopes
    )

    try:
        creds = get_credentials(email, scopes=scopes or None)
        print(f"Authenticated {prefix} ({email}) successfully.")

        # Show granted scopes — creds.scopes echoes the REQUESTED set even
        # when Google under-grants; the actual grant is in granted_scopes.
        if creds.granted_scopes is not None:
            granted = set(creds.granted_scopes)
        else:
            granted = set(creds.scopes or [])
        scope_labels = sorted(s.rsplit("/", 1)[-1] for s in granted)
        print(f"Scopes: {', '.join(scope_labels)}")

        # Verify Google granted everything we asked for
        missing = set(scopes) - granted
        if missing:
            missing_labels = ", ".join(sorted(s.rsplit("/", 1)[-1] for s in missing))
            print(f"Warning: Google granted fewer scopes than requested — missing: {missing_labels}")
            print("  The OAuth app registration (Google Cloud console → OAuth consent screen)")
            print("  or the Workspace admin policy for this domain likely blocks these scopes.")

    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"Authentication failed for {prefix}: {exc}")
        sys.exit(1)


def _auth_o365(prefix: str, cfg: dict, no_calendar: bool) -> None:
    """Authenticate an O365 source (o365 or o365cal)."""
    from ts4k.auth.microsoft import get_credentials as get_ms_credentials
    from ts4k.core.levels import scopes_for, parse_level, AccessLevel
    from ts4k.state import sources as src_mod

    client_id = cfg.get("client_id", "")
    tenant_id = cfg.get("tenant_id", "common") or "common"

    if not client_id:
        print(f"Error: source '{prefix}' is missing client_id.")
        print(f"Fix it: ts4k src add {prefix} o365 client_id=<id> tenant_id=<tid>")
        sys.exit(1)

    # Build scopes from source level
    source_level = cfg.get("level")
    provider = cfg.get("provider", "o365")
    scopes = scopes_for(provider, parse_level(source_level)) or []

    # Include calendar scopes by default
    if not no_calendar:
        cal_readonly_scopes = scopes_for("o365cal", AccessLevel.READONLY)
        scopes.extend(s for s in cal_readonly_scopes if s not in scopes)

        # Collect higher o365cal scopes if they exist for this client_id
        all_sources = src_mod.list_all()
        for pfx, src_cfg in all_sources.items():
            if src_cfg.get("provider") == "o365cal" and src_cfg.get("client_id") == client_id:
                cal_level = parse_level(src_cfg.get("level"))
                cal_scopes = scopes_for("o365cal", cal_level)
                scopes.extend(s for s in cal_scopes if s not in scopes)

    try:
        result = get_ms_credentials(client_id, tenant_id=tenant_id, scopes=scopes or None)
        print(f"Authenticated {prefix} (client {client_id[:8]}...) successfully.")

    except Exception as exc:
        print(f"Authentication failed for {prefix}: {exc}")
        sys.exit(1)

    # Re-auth may have signed in a different account under the same app
    # registration. /me sources are identified by the recorded email for
    # cache invalidation (ts4k#87), so record the account from the token
    # result that just authenticated — the raw cache scan is only a
    # fallback, since with multiple cached accounts its first entry need
    # not be the one these credentials represent.
    if provider == "o365" and not cfg.get("mailbox"):
        username = (result.get("id_token_claims") or {}).get("preferred_username")
        if not username:
            from ts4k.commands import _resolve_o365_username
            username = _resolve_o365_username(cfg)
        if username and username != cfg.get("email"):
            src_mod.update_fields(prefix, email=username)
            print(f"Recorded account: {username}")


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
    wn.add_argument("--threads", action="store_true", help="One row per thread (refs resolve to threads)")
    _add_common_args(wn)
    wn.set_defaults(func=_cmd_whatsnew)

    # --- get / g ---
    get = subparsers.add_parser(
        "get", aliases=["g"],
        help="Read a single message",
        description="Retrieve the full content of a single message by native ID or ref number. Ref numbers come from whatsnew or list output.",
        epilog=(
            "examples:\n"
            "  ts4k get 3                        # ref #3 from last list\n"
            "  ts4k g 7 -k life                  # ref #7 from 'life' whatsnew\n"
            "  ts4k get g:18f3a2b1c4d5e6f7       # by native Gmail ID\n"
            "  ts4k g 3 -k work -f json          # ref #3, JSON output\n"
            "  ts4k get 3 --readable             # human-readable body\n"
            "  ts4k get w:abc123 --media         # download media, print local path"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    get.add_argument("id", help="Message ID (e.g. g:abc123) or ref number (e.g. 7)")
    get.add_argument("--key", "-k", help="Whatsnew key for ref lookup (e.g. life)")
    get.add_argument(
        "--readable", action="store_true", default=False,
        help="Preserve paragraph breaks, bold/italic markdown, and real "
             "markdown tables (for human display, not token-optimized)",
    )
    get.add_argument(
        "--body-mode", choices=["compact", "readable"], default=None,
        help="Explicit body mode (overrides --readable): compact (default) or readable",
    )
    get.add_argument(
        "--media", action="store_true", default=False,
        help="Download the message's media file instead of its body; prints "
             "the local file path (saved under ~/.config/ts4k/media/)",
    )
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
            "  ts4k t w:chat123 --tail 5        # last 5 msgs in WhatsApp chat\n"
            "  ts4k t w:chat123 --format convo  # compact one-line-per-message view"
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
        help="Search and list messages (all filters stack)",
        description="Search messages with stackable filters. Combines time range, sender, domain, and query filters. Refs accumulate across calls.",
        epilog=(
            "examples:\n"
            "  ts4k l --since 2d                        # last 2 days\n"
            "  ts4k l --from boss@co.com --since 1w     # from boss, last week\n"
            "  ts4k l --domain co.com -n 50             # by domain, up to 50\n"
            "  ts4k l -q invoice -s g -n 10             # Gmail search, 10 results\n"
            "  ts4k l --since 6h -s g -k work           # last 6h Gmail, refs under 'work'\n"
            "  ts4k l --since 2d --threads              # one row per thread\n"
            "  ts4k l -q 'subject:urgent' -f json       # query, JSON output"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ls.add_argument("--since", help="Time range: 2d, 6h, 1w, ISO timestamp, or 'all'")
    ls.add_argument("--query", "-q", help="Search query (provider-native syntax)")
    ls.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
    ls.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all (default: all)")
    ls.add_argument("--from", dest="sender", help="Filter by sender email address")
    ls.add_argument("--domain", help="Filter by sender domain (e.g. example.com)")
    ls.add_argument("--key", "-k", help="Accumulate refs under a key (use with get -k KEY N)")
    ls.add_argument("--threads", action="store_true", help="One row per thread (refs resolve to threads)")
    _add_common_args(ls)
    ls.set_defaults(func=_cmd_list)

    # --- sources / src ---
    sr = subparsers.add_parser(
        "sources", aliases=["src"],
        help="Manage source config",
        description="Manage messaging sources. Each source has a short prefix (g, w, o) used as a namespace for message IDs.",
        epilog=(
            "examples:\n"
            "  ts4k src list                    # show configured sources + activity\n"
            "  ts4k src add g gmail you@gmail.com\n"
            "  ts4k src note oh \"mostly DMARC reports\"\n"
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
            "  whatsapp: bridge_url, bridge_token, bridge_token_file, transport (http|stdio)\n"
            "            transport=stdio also needs mcp_cwd, server_command\n"
            "  o365:     client_id (required), tenant_id, mailbox\n"
            "  github:   token or token_file (required unless GITHUB_TOKEN is set)\n"
            "  http:     url (required), header (auth header 'Name: value'; repeat or comma-separate for multiple)\n"
            "  apple/icloud: email (required), calendar_id, calendar_name  → generic caldav provider\n"
            "  apple-contacts: email (required)  → generic carddav provider (ts4k c sync)\n"
            "\n"
            "examples:\n"
            '  ts4k src add g gmail email=you@gmail.com\n'
            '  ts4k src add w whatsapp bridge_token_file=/path/to/whatsapp-bridge/store/api_token\n'
            '  ts4k src add h http url=https://example.com/api/notifications header="X-Api-Key: abc123"\n'
            '  ts4k src add cc apple email=you@icloud.com\n'
            '  ts4k src add ic apple-contacts email=you@icloud.com\n'
            "\n"
            "List fields (server_command) are auto-split on spaces.\n"
            "A bare email (user@example.com) is treated as email=user@example.com."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sr_add.add_argument("prefix", help="Source prefix (e.g. g, gn, w)")
    sr_add.add_argument("provider", help="Provider: gmail, o365, whatsapp, github, apple/icloud/caldav, apple-contacts/carddav")
    sr_add.add_argument("params", nargs="*", help="key=value pairs or bare email")

    sr_rm = sr_sub.add_parser("rm", help="Remove a source",
        description="Remove a configured source by its prefix.")
    sr_rm.add_argument("prefix", help="Source prefix to remove")

    sr_note = sr_sub.add_parser(
        "note", help="Set a noise/activity note on a source",
        description="Attach a free-text note to a source (e.g. a known noise pattern). "
                    "Surfaced in `ts4k sources`. Only the note is changed — all other "
                    "fields on the source are preserved.",
        epilog=(
            "examples:\n"
            '  ts4k src note oh "mostly DMARC reports"\n'
            "  ts4k src note oh              # clears the note"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sr_note.add_argument("prefix", help="Source prefix")
    sr_note.add_argument("text", nargs="*", help="Note text (omit to clear)")

    sr_sub.add_parser("list", help="List all configured sources",
        description="Show all configured sources with their prefixes, providers, parameters, "
                    "and cache-derived activity (message count, newest cached date, active/low/empty).")

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
            "  ts4k c list                       # show all contacts\n"
            "  ts4k c sync                       # preview an iCloud address book import\n"
            "  ts4k c sync --apply               # commit the proposed links"
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

    ct_sync = ct_sub.add_parser("sync", help="Import an address book from CardDAV",
        description="Fetch an iCloud/CardDAV address book and propose alias links. "
                    "Prints proposed links, conflicts, and skipped records; writes "
                    "nothing unless --apply is given. Existing links are never "
                    "overwritten — conflicts are reported and left alone.")
    ct_sync.add_argument("--source", "-s",
                         help="CardDAV source prefix (default: the only one configured)")
    ct_sync.add_argument("--apply", action="store_true",
                         help="Commit the proposed links to the contact map")
    ct_sync.set_defaults(func=_cmd_contacts_sync)

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
            "  ts4k f add-category ci           # skip GitHub CI notifications\n"
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
        ("add-category", "Add message category to skip list (e.g. ci)"),
        ("rm-category", "Remove category from skip list"),
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

    # --- manage / m ---
    mg = subparsers.add_parser(
        "manage", aliases=["m"],
        help="Manage mailbox (archive, label, mark read/unread, trash)",
        description="Non-destructive mailbox management. All actions are reversible. Requires source level >= modify.",
        epilog=(
            "actions:\n"
            "  archive      Remove from inbox (keep in mailbox)\n"
            "  unarchive    Return to inbox\n"
            "  label        Add a label/category (--label required)\n"
            "  unlabel      Remove a label/category (--label required)\n"
            "  read         Mark as read\n"
            "  unread       Mark as unread\n"
            "  trash        Move to trash (recoverable)\n"
            "  move         Move to folder (--folder required, O365 only)\n"
            "  list-labels  List available labels/categories\n"
            "\n"
            "examples:\n"
            "  ts4k m archive 1,2,3                 # batch archive by ref\n"
            "  ts4k m label 5 --label llm-garbage    # add label by ref\n"
            "  ts4k m read 1,2,3 -k work             # use refs from key 'work'\n"
            "  ts4k m archive 1 --thread             # archive the whole thread\n"
            "  ts4k m archive g:abc123               # by native ID\n"
            "  ts4k m list-labels g:any               # list labels for source g\n"
            "  ts4k m archive 1 --dry-run             # preview without acting"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mg.add_argument("action", choices=[
        "archive", "unarchive", "label", "unlabel",
        "read", "unread", "trash", "move", "list-labels",
    ])
    mg.add_argument("id", help="Message ID(s), comma-separated for batch")
    mg.add_argument("--label", "-l", help="Label/category name (for label/unlabel)")
    mg.add_argument("--folder", help="Folder name (for move, O365)")
    mg.add_argument("--key", "-k", help="Ref key for resolving short refs (e.g. life)")
    mg.add_argument("--thread", action="store_true", help="Apply to every message in the thread (Gmail only)")
    mg.add_argument("--dry-run", action="store_true", help="Preview actions without executing")
    mg.set_defaults(func=_cmd_manage_async)

    # --- draft / d ---
    dr = subparsers.add_parser(
        "draft", aliases=["d"],
        help="Create draft messages (never sends)",
        description="Create draft messages in your mailbox. Requires source level >= draft. ts4k NEVER sends messages.",
        epilog=(
            "examples:\n"
            '  ts4k d create -s g --to alice@x.com --subject "Hi" --body "Hello"\n'
            '  ts4k d create -s g --reply-to g:abc123 --body "Sounds good!"\n'
            '  ts4k d create -s o --to bob@co.com --subject "FYI" --body "See attached"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dr_sub = dr.add_subparsers(dest="action")
    dr_create = dr_sub.add_parser("create", help="Create a new draft")
    dr_create.add_argument("--source", "-s", required=True, help="Source prefix (e.g. g, o)")
    dr_create.add_argument("--to", required=True, help="Recipient email address")
    dr_create.add_argument("--subject", default="", help="Subject line")
    dr_create.add_argument("--body", required=True, help="Message body text")
    dr_create.add_argument("--reply-to", help="Message ID to reply to (threads the draft)")
    dr_create.add_argument("--key", "-k", help="Ref key for resolving short refs (e.g. life)")
    dr.set_defaults(func=_cmd_draft_async)

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

    # -- cal -------------------------------------------------------------------
    cal_parser = subparsers.add_parser(
        "cal",
        help="Calendar events",
        description="View and manage calendar events across configured calendar sources (Google Calendar and O365).",
        epilog=(
            "examples:\n"
            "  ts4k cal                             # today's events\n"
            "  ts4k cal today -s gc                 # today from source 'gc'\n"
            "  ts4k cal tomorrow                    # tomorrow's events\n"
            "  ts4k cal week                        # this week's events\n"
            "  ts4k cal next -n 5                   # next 5 events\n"
            "  ts4k cal range --from 2026-03-15 --to 2026-03-20\n"
            "  ts4k cal event 1                     # detail for ref #1\n"
            "  ts4k cal setup                       # discover & add calendar sources\n"
            '  ts4k cal create -s gc --title "Mtg" --start 2026-03-12T10:00 --end 2026-03-12T11:00\n'
            "  ts4k cal update 1 --title 'New title'\n"
            "  ts4k cal rsvp 1 --status accepted"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cal_parser.add_argument("--source", "-s", default=None)
    cal_parser.add_argument("--format", "-f", default="pipe")
    cal_subs = cal_parser.add_subparsers(dest="cal_cmd")
    cal_parser.set_defaults(func=_cmd_cal)

    # cal today (default)
    cal_today_p = cal_subs.add_parser("today", help="Today's events")
    cal_today_p.add_argument("--source", "-s", default=None)
    cal_today_p.add_argument("--format", "-f", default="pipe")
    cal_today_p.set_defaults(func=_cmd_cal_today)

    # cal tomorrow
    cal_tmrw_p = cal_subs.add_parser("tomorrow", help="Tomorrow's events")
    cal_tmrw_p.add_argument("--source", "-s", default=None)
    cal_tmrw_p.add_argument("--format", "-f", default="pipe")
    cal_tmrw_p.set_defaults(func=_cmd_cal_tomorrow)

    # cal week
    cal_week_p = cal_subs.add_parser("week", help="This week's events")
    cal_week_p.add_argument("--source", "-s", default=None)
    cal_week_p.add_argument("--format", "-f", default="pipe")
    cal_week_p.set_defaults(func=_cmd_cal_week)

    # cal next
    cal_next_p = cal_subs.add_parser("next", help="Next N events")
    cal_next_p.add_argument("count", nargs="?", type=int, default=None, help="Number of events (default 10)")
    cal_next_p.add_argument("-n", type=int, default=None, help="Number of events (default 10)")
    cal_next_p.add_argument("--source", "-s", default=None)
    cal_next_p.add_argument("--format", "-f", default="pipe")
    cal_next_p.set_defaults(func=_cmd_cal_next)

    # cal range
    cal_range_p = cal_subs.add_parser("range", help="Events in date range")
    cal_range_p.add_argument("--from", dest="from_date", required=True)
    cal_range_p.add_argument("--to", dest="to_date", required=True)
    cal_range_p.add_argument("--source", "-s", default=None)
    cal_range_p.add_argument("--format", "-f", default="pipe")
    cal_range_p.set_defaults(func=_cmd_cal_range)

    # cal event
    cal_event_p = cal_subs.add_parser("event", help="Event detail")
    cal_event_p.add_argument("ref", help="Event ref or ID")
    cal_event_p.add_argument("--source", "-s", default=None)
    cal_event_p.add_argument("--format", "-f", default="pipe")
    cal_event_p.set_defaults(func=_cmd_cal_event)

    # cal setup
    cal_setup_p = cal_subs.add_parser("setup", help="Discover and add calendar sources")
    cal_setup_p.set_defaults(func=_cmd_cal_setup)

    # cal create
    cal_create_p = cal_subs.add_parser("create", help="Create an event")
    cal_create_p.add_argument("--title", required=True)
    cal_create_p.add_argument("--start", required=True)
    cal_create_p.add_argument("--end", required=True)
    cal_create_p.add_argument("--description", default=None)
    cal_create_p.add_argument("--location", default=None)
    cal_create_p.add_argument("--attendees", default=None, help="Comma-separated emails")
    cal_create_p.add_argument("--source", "-s", required=True)
    cal_create_p.set_defaults(func=_cmd_cal_create)

    # cal update
    cal_update_p = cal_subs.add_parser("update", help="Update an event")
    cal_update_p.add_argument("ref", help="Event ref or ID")
    cal_update_p.add_argument("--title", default=None)
    cal_update_p.add_argument("--start", default=None)
    cal_update_p.add_argument("--end", default=None)
    cal_update_p.add_argument("--description", default=None)
    cal_update_p.add_argument("--location", default=None)
    cal_update_p.add_argument("--source", "-s", default=None)
    cal_update_p.set_defaults(func=_cmd_cal_update)

    # cal rsvp
    cal_rsvp_p = cal_subs.add_parser("rsvp", help="RSVP to an event")
    cal_rsvp_p.add_argument("ref", help="Event ref or ID")
    cal_rsvp_p.add_argument("--status", required=True, choices=["accepted", "declined", "tentative"])
    cal_rsvp_p.add_argument("--source", "-s", default=None)
    cal_rsvp_p.set_defaults(func=_cmd_cal_rsvp)

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
        help="Authenticate or validate tokens",
        description=(
            "Authenticate with a messaging platform or validate existing tokens.\n\n"
            "Target resolution:\n"
            "  1. Source prefix first (g, gn, o, oc) — auths that specific source\n"
            "  2. Provider name (gmail, o365) — auths all sources of that provider\n"
            "  3. Omitted + --check — validates all sources\n"
            "  4. Omitted without --check — shows this help"
        ),
        epilog=(
            "examples:\n"
            "  ts4k auth g                  Auth source 'g' (resolves email from config)\n"
            "  ts4k auth gmail              Auth all Gmail sources\n"
            "  ts4k auth o                  Auth source 'o' (O365, device code flow)\n"
            "  ts4k auth --check            Validate all sources, no re-auth\n"
            "  ts4k auth g --check          Validate just source 'g'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    au.add_argument("target", nargs="?", default=None,
                    help="Source prefix (g, o) or provider name (gmail, o365)")
    au.add_argument("--check", action="store_true",
                    help="Validate tokens without re-auth")
    au.add_argument("--no-calendar", action="store_true",
                    help="Skip requesting calendar scopes")
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

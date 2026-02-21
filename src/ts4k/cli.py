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

from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig
from ts4k.adapters.whatsapp import WhatsAppAdapter, WhatsAppAdapterConfig
from ts4k.core.format import format_listing, format_message, format_thread
from ts4k.core.normalize import normalize, normalize_headers
from ts4k.core.filter import apply_filters
from ts4k.state import contacts, filters, sources, stats, watermarks

logger = logging.getLogger("ts4k")


# ---------------------------------------------------------------------------
# Source config resolution
# ---------------------------------------------------------------------------


def _ensure_sources() -> dict[str, dict[str, Any]]:
    """Load source config from sources.json."""
    return sources.list_all()


def _make_adapter(prefix: str, cfg: dict[str, Any]) -> GmailAdapter | WhatsAppAdapter | O365Adapter | None:
    """Create an adapter instance from a source config entry."""
    provider = cfg.get("provider", "").lower()

    if provider == "gmail":
        email = cfg.get("email")
        if not email:
            return None
        return GmailAdapter(
            GmailAdapterConfig(
                user_email=email,
                transport=cfg.get("transport", "streamable-http"),
                server_url=cfg.get("mcp_url", "http://localhost:51429/mcp"),
            ),
            prefix=prefix,
        )

    if provider == "whatsapp":
        cwd = cfg.get("mcp_cwd", "")
        if not cwd or not os.path.isdir(cwd):
            return None
        cmd = cfg.get("server_command", ["uv", "run", "main.py"])
        return WhatsAppAdapter(
            WhatsAppAdapterConfig(server_command=cmd, server_cwd=cwd),
            prefix=prefix,
        )

    if provider == "o365":
        # Server command: single string or list from config, else default
        raw_cmd = cfg.get("server_command")
        if isinstance(raw_cmd, str):
            server_command = [raw_cmd]
        elif isinstance(raw_cmd, list):
            server_command = raw_cmd
        else:
            server_command = ["npx", "-y", "@softeria/ms-365-mcp-server"]

        # Server args: skip defaults if using a custom command (e.g. start.bat
        # that already embeds --preset and --org-mode)
        server_args = cfg.get("server_args", []) if raw_cmd else ["--preset", "mail", "--org-mode"]

        # Build server_env from source config credentials
        server_env: dict[str, str] | None = None
        client_id = cfg.get("client_id")
        tenant_id = cfg.get("tenant_id")
        if client_id or tenant_id:
            server_env = {}
            if client_id:
                server_env["MS365_MCP_CLIENT_ID"] = client_id
            if tenant_id:
                server_env["MS365_MCP_TENANT_ID"] = tenant_id

        return O365Adapter(
            O365AdapterConfig(
                server_command=server_command,
                server_args=server_args,
                mailbox=cfg.get("mailbox"),
                server_cwd=cfg.get("mcp_cwd"),
                server_env=server_env,
            ),
            prefix=prefix,
        )

    logger.warning("Unknown provider %r for source %r", provider, prefix)
    return None


def _resolve_prefixes(args: argparse.Namespace) -> list[str]:
    """Determine which source prefixes to query based on --source flag.

    Accepts:
    - A specific prefix: ``--source g``, ``--source gn``
    - A provider name: ``--source gmail`` (all gmail sources)
    - ``all`` (default): every configured source
    """
    source = getattr(args, "source", None) or "all"
    source = source.lower().strip()
    all_cfg = _ensure_sources()

    if source == "all":
        return list(all_cfg.keys())

    # Exact prefix match
    if source in all_cfg:
        return [source]

    # Provider name match (e.g. "gmail" → all gmail prefixes)
    provider_map = {"wa": "whatsapp", "outlook": "o365", "office": "o365", "365": "o365"}
    provider = provider_map.get(source, source)
    matches = [
        p for p, c in all_cfg.items()
        if c.get("provider", "").lower() == provider
    ]
    if matches:
        return matches

    # Unknown — return as-is, will fail gracefully downstream
    return [source]


def _prefix_from_id(prefixed_id: str, all_cfg: dict[str, dict[str, Any]]) -> str:
    """Extract source prefix from a prefixed ID like 'g:xxx' or 'gn:xxx'.

    Tries longest-matching configured prefix first.
    """
    # Sort prefixes by length descending so "gn:" matches before "g:"
    for prefix in sorted(all_cfg.keys(), key=len, reverse=True):
        if prefixed_id.startswith(f"{prefix}:"):
            return prefix
    # Fallback: take everything before first ':'
    if ":" in prefixed_id:
        return prefixed_id.split(":")[0]
    return ""


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


def _since_to_gmail_query(since: str | None, prefix: str = "g") -> str:
    """Convert a --since value to a Gmail search query fragment."""
    if since is None:
        wm = watermarks.get(prefix)
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


def _since_to_iso(since: str | None, prefix: str) -> str | None:
    """Convert a --since value to an ISO timestamp for adapters that take ISO."""
    if since is None:
        return watermarks.get(prefix)

    if since.endswith("d") and since[:-1].isdigit():
        from datetime import datetime, timedelta, timezone
        days = int(since[:-1])
        dt = datetime.now(timezone.utc) - timedelta(days=days)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return since


def _maybe_filter(messages: list[dict], args: argparse.Namespace) -> list[dict]:
    """Apply skip filters if --filter / -F was passed."""
    if getattr(args, "filter", False):
        return apply_filters(messages, filters.get_config())
    return messages


def _raw_bytes(messages: list[dict]) -> int:
    """Estimate raw bytes from message dicts (pre-format size)."""
    import json as _json
    return sum(len(_json.dumps(m).encode("utf-8")) for m in messages)


def _record_stats(
    command: str, messages: list[dict], output: str,
) -> None:
    """Record stats for a batch of messages grouped by source."""
    out_bytes = len(output.encode("utf-8"))
    # Group messages by source for per-source tracking
    by_source: dict[str, list[dict]] = {}
    for msg in messages:
        src = msg.get("source", "") or ""
        if not src and "id" in msg:
            mid = msg["id"]
            src = mid.split(":")[0] if ":" in mid else ""
        by_source.setdefault(src, []).append(msg)

    for source, msgs in by_source.items():
        raw = _raw_bytes(msgs)
        # Apportion output bytes by proportion of messages
        proportion = len(msgs) / len(messages) if messages else 0
        stats.record(
            command=command,
            source=source or "?",
            bytes_in=raw,
            bytes_out=round(out_bytes * proportion),
            messages=len(msgs),
        )


# ---------------------------------------------------------------------------
# Parallel adapter helpers
# ---------------------------------------------------------------------------


async def _fetch_whatsnew_for_source(
    prefix: str, cfg: dict[str, Any], since: str | None, count: int
) -> list[dict]:
    """Fetch new messages from a single source. Returns normalized dicts."""
    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return []

    provider = cfg.get("provider", "").lower()
    try:
        async with adapter:
            if provider == "gmail":
                query = _since_to_gmail_query(since, prefix)
                listing = await adapter.list_messages(query=query, count=count)
                if not listing:
                    return []
                messages = []
                for entry in listing[:count]:
                    try:
                        msg = await adapter.read_message(entry["id"])
                        msg = _normalize_message(msg)
                        msg.setdefault("source", prefix)
                        messages.append(msg)
                    except Exception as exc:
                        logger.warning("[%s] fetch %s: %s", prefix, entry["id"], exc)
                return messages

            else:  # whatsapp and others that use ISO timestamps
                iso_since = _since_to_iso(since, prefix)
                listing = await adapter.whatsnew(since=iso_since)
                if not listing:
                    return []
                messages = []
                for entry in listing[:count]:
                    msg = _normalize_message(entry)
                    msg.setdefault("source", prefix)
                    messages.append(msg)
                return messages

    except Exception as exc:
        logger.warning("[%s] adapter failed: %s", prefix, exc)
        return []


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    """Handle the whatsnew / wn command."""
    fmt = getattr(args, "format", "pipe") or "pipe"
    count = getattr(args, "count", 20) or 20
    since = getattr(args, "since", None)
    active_prefixes = _resolve_prefixes(args)
    all_cfg = _ensure_sources()

    # Build fetch tasks per source — all in parallel
    tasks: list[asyncio.Task] = []
    task_prefixes: list[str] = []

    for prefix in active_prefixes:
        cfg = all_cfg.get(prefix)
        if cfg:
            tasks.append(asyncio.create_task(
                _fetch_whatsnew_for_source(prefix, cfg, since, count)
            ))
            task_prefixes.append(prefix)

    if not tasks:
        print("No sources configured. Run: ts4k src add <prefix> <provider> ...", file=sys.stderr)
        return

    # Run in parallel
    results = await asyncio.gather(*tasks)

    # Merge and sort by date descending
    all_messages: list[dict] = []
    for msgs in results:
        all_messages.extend(msgs)

    all_messages.sort(key=lambda m: m.get("date", ""), reverse=True)
    all_messages = all_messages[:count]
    all_messages = _maybe_filter(all_messages, args)

    if not all_messages:
        print("No new messages.", file=sys.stderr)
        return

    output = format_listing(all_messages, fmt=fmt)
    print(output)
    _record_stats("wn", all_messages, output)

    # Update watermarks per source (always, even when filtering)
    for prefix, msgs in zip(task_prefixes, results):
        if msgs:
            newest = max(m.get("date", "") for m in msgs)
            if newest:
                watermarks.update(prefix, newest)


async def _cmd_get(args: argparse.Namespace) -> None:
    """Handle the get / g command."""
    fmt = getattr(args, "format", "pipe") or "pipe"
    msg_id = args.id
    all_cfg = _ensure_sources()
    prefix = _prefix_from_id(msg_id, all_cfg)
    cfg = all_cfg.get(prefix)

    if not cfg:
        print(f"Error: no source configured for prefix {prefix!r}.", file=sys.stderr)
        sys.exit(1)

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        print(f"Error: source {prefix!r} not available.", file=sys.stderr)
        sys.exit(1)

    async with adapter:
        msg = await adapter.read_message(msg_id)
        msg = _normalize_message(msg)
        output = format_message(msg, fmt=fmt)
        print(output)
        _record_stats("g", [msg], output)


async def _cmd_thread(args: argparse.Namespace) -> None:
    """Handle the thread / t command."""
    fmt = getattr(args, "format", "pipe") or "pipe"
    thread_id = args.id
    all_cfg = _ensure_sources()
    prefix = _prefix_from_id(thread_id, all_cfg)
    cfg = all_cfg.get(prefix)

    if not cfg:
        print(f"Error: no source configured for prefix {prefix!r}.", file=sys.stderr)
        sys.exit(1)

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        print(f"Error: source {prefix!r} not available.", file=sys.stderr)
        sys.exit(1)

    async with adapter:
        thread = await adapter.read_thread(thread_id)
        thread = _normalize_thread(thread)
        output = format_thread(thread, fmt=fmt)
        print(output)
        _record_stats("t", thread.get("messages", []), output)


async def _cmd_list(args: argparse.Namespace) -> None:
    """Handle the list / l command."""
    fmt = getattr(args, "format", "pipe") or "pipe"
    count = getattr(args, "count", 20) or 20
    query = getattr(args, "query", None)
    active_prefixes = _resolve_prefixes(args)
    all_cfg = _ensure_sources()

    all_messages: list[dict] = []

    for prefix in active_prefixes:
        cfg = all_cfg.get(prefix)
        if not cfg:
            continue
        adapter = _make_adapter(prefix, cfg)
        if adapter is None:
            continue

        provider = cfg.get("provider", "").lower()
        try:
            async with adapter:
                listing = await adapter.list_messages(query=query, count=count)
                for entry in (listing or []):
                    if provider == "gmail":
                        try:
                            msg = await adapter.read_message(entry["id"])
                            msg = _normalize_message(msg)
                            msg.setdefault("source", prefix)
                            all_messages.append(msg)
                        except Exception as exc:
                            logger.warning("[%s] fetch %s: %s", prefix, entry["id"], exc)
                    else:
                        msg = _normalize_message(entry)
                        msg.setdefault("source", prefix)
                        all_messages.append(msg)
        except Exception as exc:
            logger.warning("[%s] adapter failed: %s", prefix, exc)

    if not all_messages:
        print("No messages found.", file=sys.stderr)
        return

    all_messages.sort(key=lambda m: m.get("date", ""), reverse=True)
    all_messages = _maybe_filter(all_messages[:count], args)

    if not all_messages:
        print("No messages found.", file=sys.stderr)
        return

    output = format_listing(all_messages, fmt=fmt)
    print(output)
    _record_stats("l", all_messages, output)


def _cmd_help(args: argparse.Namespace) -> None:
    """Handle the help / h command — show status and quick reference."""
    config_dir = os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")
    all_cfg = _ensure_sources()
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


def _cmd_status(args: argparse.Namespace) -> None:
    """Handle the status / st command — operational state summary."""
    from ts4k.core.format import estimate_size

    config_dir = os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")
    all_cfg = _ensure_sources()
    wm = watermarks.all()

    # Sources
    print("Sources:")
    if all_cfg:
        for prefix, cfg in sorted(all_cfg.items()):
            provider = cfg.get("provider", "?")
            detail = cfg.get("email") or cfg.get("mailbox") or cfg.get("mcp_cwd") or ""
            ok = True
            if provider == "whatsapp":
                cwd = cfg.get("mcp_cwd", "")
                ok = bool(cwd) and os.path.isdir(cwd)
            elif provider == "o365":
                ok = bool(cfg.get("mailbox") or cfg.get("client_id"))
            status = "ok" if ok else "not found"
            wm_ts = wm.get(prefix, "")
            wm_str = f"  wm: {wm_ts}" if wm_ts else ""
            print(f"  {prefix}: {provider} [{status}] ({detail}){wm_str}")
    else:
        print("  (none — run: ts4k src add <prefix> <provider> ...)")

    # Contacts
    all_contacts = contacts.list_all()
    total_idents = sum(len(ids) for ids in all_contacts.values())
    print()
    print(f"Contacts: {len(all_contacts)} aliases, {total_idents} identifiers")

    # Filters
    fconfig = filters.get_config()
    active_rules = (
        len(fconfig.get("skip_senders", []))
        + len(fconfig.get("skip_domains", []))
        + len(fconfig.get("skip_patterns", []))
        + (1 if fconfig.get("skip_groups") else 0)
    )
    print(f"Filters:  {active_rules} active rules (use -F to apply)")

    # Stats
    st = stats.get_all()
    total_in = st.get("total_bytes_in", 0)
    total_out = st.get("total_bytes_out", 0)
    total_msgs = st.get("total_messages", 0)
    pct = stats.savings_pct()

    print()
    print("Stats:")
    if total_msgs > 0:
        print(f"  Messages processed: {total_msgs}")
        print(f"  Bytes in:  {estimate_size(total_in)} ({total_in:,})")
        print(f"  Bytes out: {estimate_size(total_out)} ({total_out:,})")
        print(f"  Savings:   {pct}%")

        by_source = st.get("by_source", {})
        if by_source:
            print()
            print("  By source:")
            for src, data in sorted(by_source.items()):
                label = {"g": "Gmail", "w": "WhatsApp", "o": "O365"}.get(src, src)
                src_in = data.get("bytes_in", 0)
                src_pct = round((1 - data.get("bytes_out", 0) / src_in) * 100, 1) if src_in else 0
                print(f"    {label}: {data.get('messages', 0)} msgs, "
                      f"{estimate_size(src_in)} in, {src_pct}% savings")

        by_cmd = st.get("by_command", {})
        if by_cmd:
            print()
            print("  By command:")
            for cmd, data in sorted(by_cmd.items()):
                print(f"    {cmd}: {data.get('calls', 0)} calls, "
                      f"{estimate_size(data.get('bytes_in', 0))} in -> "
                      f"{estimate_size(data.get('bytes_out', 0))} out")
    else:
        print("  (no data yet — run some commands first)")

    print()
    print(f"Config: {config_dir}")


def _cmd_sources(args: argparse.Namespace) -> None:
    """Handle the src command — manage source config."""
    action = getattr(args, "action", None)

    if action == "add":
        prefix = args.prefix
        provider = args.provider.lower()
        # Parse key=value pairs from extra args
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
        # Default: list
        _cmd_sources(argparse.Namespace(action="list", prefix=None, provider=None, params=None))


async def _cmd_discover_o365(args: argparse.Namespace) -> None:
    """Discover O365 mailboxes for the authenticated user."""
    all_cfg = _ensure_sources()

    # Find an existing O365 source to borrow credentials from, or use defaults
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

    # Suggest source prefixes
    all_emails = [primary] + aliases
    existing_mailboxes = {
        cfg.get("mailbox", "").lower()
        for cfg in all_cfg.values()
        if cfg.get("provider", "").lower() == "o365"
    }
    used_prefixes = set(all_cfg.keys())

    print()
    print("Add as sources? (Each gets its own prefix)")

    # Auto-generate prefixes: oa, ob, oc, ...
    next_suffix = ord("a")
    for email in all_emails:
        while True:
            candidate = f"o{chr(next_suffix)}"
            next_suffix += 1
            if candidate not in used_prefixes:
                break
        already = "  [already configured]" if email.lower() in existing_mailboxes else ""
        print(f"  {candidate} → {email}{already}")


def _cmd_filter(args: argparse.Namespace) -> None:
    """Handle the filter / f command."""
    action = getattr(args, "action", None)

    if action == "add-sender":
        result = filters.add_sender(args.value)
        print(f"skip_senders: {', '.join(result)}")
    elif action == "rm-sender":
        result = filters.remove_sender(args.value)
        print(f"skip_senders: {', '.join(result) or '(empty)'}")
    elif action == "add-domain":
        result = filters.add_domain(args.value)
        print(f"skip_domains: {', '.join(result)}")
    elif action == "rm-domain":
        result = filters.remove_domain(args.value)
        print(f"skip_domains: {', '.join(result) or '(empty)'}")
    elif action == "add-pattern":
        result = filters.add_pattern(args.value)
        print(f"skip_patterns: {', '.join(result)}")
    elif action == "rm-pattern":
        result = filters.remove_pattern(args.value)
        print(f"skip_patterns: {', '.join(result) or '(empty)'}")
    elif action == "skip-groups":
        val = args.value.lower() in ("true", "yes", "on", "1")
        result = filters.set_skip_groups(val)
        print(f"skip_groups: {result}")
    elif action == "reset":
        filters.reset()
        print("Filters reset to defaults.")
    elif action == "show":
        config = filters.get_config()
        print(f"skip_senders:  {', '.join(config['skip_senders']) or '(none)'}")
        print(f"skip_domains:  {', '.join(config['skip_domains']) or '(none)'}")
        print(f"skip_groups:   {config['skip_groups']}")
        print(f"skip_patterns: {', '.join(config['skip_patterns']) or '(none)'}")
    else:
        # Default: show
        config = filters.get_config()
        print(f"skip_senders:  {', '.join(config['skip_senders']) or '(none)'}")
        print(f"skip_domains:  {', '.join(config['skip_domains']) or '(none)'}")
        print(f"skip_groups:   {config['skip_groups']}")
        print(f"skip_patterns: {', '.join(config['skip_patterns']) or '(none)'}")
        print()
        print("Use -F flag on wn/l to apply. Filters are OFF by default.")


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

    if args.func in (_cmd_help, _cmd_contacts, _cmd_filter, _cmd_status, _cmd_sources):
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

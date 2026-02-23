"""ts4k command router — shared command logic for CLI, MCP server, and Skill.

Each command function takes explicit parameters and returns a string (or
CommandResult dataclass).  No printing, no argparse, no sys.exit — those
belong in the caller (cli.py, server.py, etc.).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig
from ts4k.adapters.whatsapp import WhatsAppAdapter, WhatsAppAdapterConfig
from ts4k.core.filter import apply_filters
from ts4k.core.format import (
    estimate_size,
    format_listing,
    format_message,
    format_thread,
)
from ts4k.core.normalize import normalize, normalize_headers
from ts4k.state import batch, cache, contacts, filters, sources, stats, watermarks

logger = logging.getLogger("ts4k")


# ---------------------------------------------------------------------------
# CommandResult
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    """Return type for commands that process messages."""

    output: str = ""
    messages_processed: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Source config resolution
# ---------------------------------------------------------------------------


def _ensure_sources() -> dict[str, dict[str, Any]]:
    """Load source config from sources.json."""
    return sources.list_all()


def _make_adapter(
    prefix: str, cfg: dict[str, Any]
) -> GmailAdapter | WhatsAppAdapter | O365Adapter | None:
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
        raw_cmd = cfg.get("server_command")
        if isinstance(raw_cmd, str):
            server_command = [raw_cmd]
        elif isinstance(raw_cmd, list):
            server_command = raw_cmd
        else:
            server_command = ["npx", "-y", "@softeria/ms-365-mcp-server"]

        server_args = (
            cfg.get("server_args", []) if raw_cmd else ["--preset", "mail", "--org-mode"]
        )

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


def _resolve_prefixes(source: str | None) -> list[str]:
    """Determine which source prefixes to query.

    Accepts a specific prefix, a provider name, or ``"all"`` (default).
    """
    source = (source or "all").lower().strip()
    all_cfg = _ensure_sources()

    if source == "all":
        return list(all_cfg.keys())

    if source in all_cfg:
        return [source]

    provider_map = {"wa": "whatsapp", "outlook": "o365", "office": "o365", "365": "o365"}
    provider = provider_map.get(source, source)
    matches = [
        p for p, c in all_cfg.items() if c.get("provider", "").lower() == provider
    ]
    if matches:
        return matches

    return [source]


def _prefix_from_id(prefixed_id: str, all_cfg: dict[str, dict[str, Any]]) -> str:
    """Extract source prefix from a prefixed ID like ``'g:xxx'``."""
    for prefix in sorted(all_cfg.keys(), key=len, reverse=True):
        if prefixed_id.startswith(f"{prefix}:"):
            return prefix
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


def _raw_bytes(messages: list[dict]) -> int:
    """Estimate raw bytes from message dicts (pre-format size)."""
    return sum(len(_json.dumps(m).encode("utf-8")) for m in messages)


def _record_stats(command: str, messages: list[dict], output: str) -> None:
    """Record stats for a batch of messages grouped by source."""
    out_bytes = len(output.encode("utf-8"))
    by_source: dict[str, list[dict]] = {}
    for msg in messages:
        src = msg.get("source", "") or ""
        if not src and "id" in msg:
            mid = msg["id"]
            src = mid.split(":")[0] if ":" in mid else ""
        by_source.setdefault(src, []).append(msg)

    for source, msgs in by_source.items():
        raw = _raw_bytes(msgs)
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
                        cache.store_message(msg.get("id", entry["id"]), msg)
                        messages.append(msg)
                    except Exception as exc:
                        logger.warning("[%s] fetch %s: %s", prefix, entry["id"], exc)
                return messages

            else:
                iso_since = _since_to_iso(since, prefix)
                listing = await adapter.whatsnew(since=iso_since)
                if not listing:
                    return []
                messages = []
                for entry in listing[:count]:
                    msg = _normalize_message(entry)
                    msg.setdefault("source", prefix)
                    cache.store_message(msg.get("id", ""), msg)
                    messages.append(msg)
                return messages

    except Exception as exc:
        logger.warning("[%s] adapter failed: %s", prefix, exc)
        return []


# ---------------------------------------------------------------------------
# Command functions
# ---------------------------------------------------------------------------


async def whatsnew(
    source: str | None = None,
    since: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    apply_filter: bool = False,
) -> CommandResult:
    """Fetch new messages, update watermarks, return formatted output."""
    active_prefixes = _resolve_prefixes(source)
    all_cfg = _ensure_sources()

    tasks: list[asyncio.Task] = []
    task_prefixes: list[str] = []

    for prefix in active_prefixes:
        cfg = all_cfg.get(prefix)
        if cfg:
            tasks.append(
                asyncio.create_task(
                    _fetch_whatsnew_for_source(prefix, cfg, since, count)
                )
            )
            task_prefixes.append(prefix)

    if not tasks:
        return CommandResult(error="No sources configured. Run: ts4k src add <prefix> <provider> ...")

    results = await asyncio.gather(*tasks)

    all_messages: list[dict] = []
    for msgs in results:
        all_messages.extend(msgs)

    all_messages.sort(key=lambda m: m.get("date", ""), reverse=True)
    all_messages = all_messages[:count]

    if apply_filter:
        all_messages = apply_filters(all_messages, filters.get_config())

    if not all_messages:
        return CommandResult(error="No new messages.")

    output = format_listing(all_messages, fmt=fmt)
    _record_stats("wn", all_messages, output)

    # Update watermarks per source
    for prefix, msgs in zip(task_prefixes, results):
        if msgs:
            newest = max(m.get("date", "") for m in msgs)
            if newest:
                watermarks.update(prefix, newest)

    return CommandResult(output=output, messages_processed=len(all_messages))


async def get_message(msg_id: str, fmt: str = "pipe") -> CommandResult:
    """Read a single message by prefixed ID."""
    # Read-through: check cache first
    cached = cache.get_message(msg_id)
    if cached and cached.get("body"):
        output = format_message(cached, fmt=fmt)
        _record_stats("g", [cached], output)
        return CommandResult(output=output, messages_processed=1)

    all_cfg = _ensure_sources()
    prefix = _prefix_from_id(msg_id, all_cfg)
    cfg = all_cfg.get(prefix)

    if not cfg:
        return CommandResult(error=f"No source configured for prefix {prefix!r}.")

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return CommandResult(error=f"Source {prefix!r} not available.")

    async with adapter:
        msg = await adapter.read_message(msg_id)
        msg = _normalize_message(msg)
        cache.store_message(msg_id, msg)
        output = format_message(msg, fmt=fmt)
        _record_stats("g", [msg], output)

    return CommandResult(output=output, messages_processed=1)


async def get_thread(thread_id: str, fmt: str = "pipe") -> CommandResult:
    """Read a thread/conversation by prefixed ID."""
    all_cfg = _ensure_sources()
    prefix = _prefix_from_id(thread_id, all_cfg)
    cfg = all_cfg.get(prefix)

    if not cfg:
        return CommandResult(error=f"No source configured for prefix {prefix!r}.")

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return CommandResult(error=f"Source {prefix!r} not available.")

    async with adapter:
        thread = await adapter.read_thread(thread_id)
        thread = _normalize_thread(thread)
        for msg in thread.get("messages", []):
            mid = msg.get("id")
            if mid:
                cache.store_message(mid, msg)
        output = format_thread(thread, fmt=fmt)
        _record_stats("t", thread.get("messages", []), output)

    return CommandResult(output=output, messages_processed=len(thread.get("messages", [])))


async def list_messages(
    source: str | None = None,
    query: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    apply_filter: bool = False,
) -> CommandResult:
    """List messages matching a query."""
    active_prefixes = _resolve_prefixes(source)
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
                for entry in listing or []:
                    if provider == "gmail":
                        try:
                            msg = await adapter.read_message(entry["id"])
                            msg = _normalize_message(msg)
                            msg.setdefault("source", prefix)
                            cache.store_message(msg.get("id", entry["id"]), msg)
                            all_messages.append(msg)
                        except Exception as exc:
                            logger.warning("[%s] fetch %s: %s", prefix, entry["id"], exc)
                    else:
                        msg = _normalize_message(entry)
                        msg.setdefault("source", prefix)
                        cache.store_message(msg.get("id", ""), msg)
                        all_messages.append(msg)
        except Exception as exc:
            logger.warning("[%s] adapter failed: %s", prefix, exc)

    if not all_messages:
        return CommandResult(error="No messages found.")

    all_messages.sort(key=lambda m: m.get("date", ""), reverse=True)
    all_messages = all_messages[:count]

    if apply_filter:
        all_messages = apply_filters(all_messages, filters.get_config())

    if not all_messages:
        return CommandResult(error="No messages found.")

    output = format_listing(all_messages, fmt=fmt)
    _record_stats("l", all_messages, output)

    return CommandResult(output=output, messages_processed=len(all_messages))


def get_status() -> str:
    """Return operational status summary as a string."""
    config_dir = os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")
    all_cfg = _ensure_sources()
    wm = watermarks.all()
    lines: list[str] = []

    # Sources
    lines.append("Sources:")
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
            lines.append(f"  {prefix}: {provider} [{status}] ({detail}){wm_str}")
    else:
        lines.append("  (none — run: ts4k src add <prefix> <provider> ...)")

    # Contacts
    all_contacts = contacts.list_all()
    total_idents = sum(len(ids) for ids in all_contacts.values())
    lines.append("")
    lines.append(f"Contacts: {len(all_contacts)} aliases, {total_idents} identifiers")

    # Filters
    fconfig = filters.get_config()
    active_rules = (
        len(fconfig.get("skip_senders", []))
        + len(fconfig.get("skip_domains", []))
        + len(fconfig.get("skip_patterns", []))
        + (1 if fconfig.get("skip_groups") else 0)
    )
    lines.append(f"Filters:  {active_rules} active rules (use -F to apply)")

    # Stats
    st = stats.get_all()
    total_in = st.get("total_bytes_in", 0)
    total_out = st.get("total_bytes_out", 0)
    total_msgs = st.get("total_messages", 0)
    pct = stats.savings_pct()

    lines.append("")
    lines.append("Stats:")
    if total_msgs > 0:
        lines.append(f"  Messages processed: {total_msgs}")
        lines.append(f"  Bytes in:  {estimate_size(total_in)} ({total_in:,})")
        lines.append(f"  Bytes out: {estimate_size(total_out)} ({total_out:,})")
        lines.append(f"  Savings:   {pct}%")

        by_source = st.get("by_source", {})
        if by_source:
            lines.append("")
            lines.append("  By source:")
            for src, data in sorted(by_source.items()):
                label = {"g": "Gmail", "w": "WhatsApp", "o": "O365"}.get(src, src)
                src_in = data.get("bytes_in", 0)
                src_pct = (
                    round((1 - data.get("bytes_out", 0) / src_in) * 100, 1) if src_in else 0
                )
                lines.append(
                    f"    {label}: {data.get('messages', 0)} msgs, "
                    f"{estimate_size(src_in)} in, {src_pct}% savings"
                )

        by_cmd = st.get("by_command", {})
        if by_cmd:
            lines.append("")
            lines.append("  By command:")
            for cmd, data in sorted(by_cmd.items()):
                lines.append(
                    f"    {cmd}: {data.get('calls', 0)} calls, "
                    f"{estimate_size(data.get('bytes_in', 0))} in -> "
                    f"{estimate_size(data.get('bytes_out', 0))} out"
                )
    else:
        lines.append("  (no data yet — run some commands first)")

    # Cache
    cs = cache.stats()
    lines.append("")
    lines.append(f"Cache: {cs['total']} messages, {cs['bodies']} bodies")
    if cs["total"]:
        for src, n in sorted(cs["by_source"].items()):
            label = {"g": "Gmail", "o": "O365"}.get(src, src)
            lines.append(f"  {label}: {n}")
        lines.append(f"  Disk: {estimate_size(cs['index_bytes'] + cs['bodies_bytes'])}")

    lines.append("")
    lines.append(f"Config: {config_dir}")

    return "\n".join(lines)


def manage_contacts(
    action: str | None = None,
    alias: str | None = None,
    identifiers: list[str] | None = None,
    term: str | None = None,
) -> str:
    """Manage contacts (link, unlink, find, list). Returns output string."""
    lines: list[str] = []

    if action == "link":
        if not alias or not identifiers:
            return "Error: alias and at least one identifier required."
        result = contacts.link(alias, *identifiers)
        return f"{alias}: {' | '.join(result)}"

    elif action == "unlink":
        if not alias:
            return "Error: alias required."
        if identifiers:
            result = contacts.unlink(alias, *identifiers)
            if result is None:
                return f"{alias}: (removed)"
            return f"{alias}: {' | '.join(result)}"
        else:
            contacts.unlink(alias)
            return f"{alias}: (removed)"

    elif action == "find":
        if not term:
            return "Error: search term required."
        results = contacts.find(term)
        if not results:
            return "No matches."
        for a, idents in sorted(results.items()):
            lines.append(f"{a}: {' | '.join(idents)}")
        return "\n".join(lines)

    else:  # "list" or default
        all_contacts = contacts.list_all()
        if not all_contacts:
            return "No contacts."
        for a, idents in sorted(all_contacts.items()):
            lines.append(f"{a}: {' | '.join(idents)}")
        return "\n".join(lines)


def manage_filters(action: str | None = None, value: str | None = None) -> str:
    """Manage skip filters. Returns output string."""
    if action == "add-sender":
        result = filters.add_sender(value or "")
        return f"skip_senders: {', '.join(result)}"
    elif action == "rm-sender":
        result = filters.remove_sender(value or "")
        return f"skip_senders: {', '.join(result) or '(empty)'}"
    elif action == "add-domain":
        result = filters.add_domain(value or "")
        return f"skip_domains: {', '.join(result)}"
    elif action == "rm-domain":
        result = filters.remove_domain(value or "")
        return f"skip_domains: {', '.join(result) or '(empty)'}"
    elif action == "add-pattern":
        result = filters.add_pattern(value or "")
        return f"skip_patterns: {', '.join(result)}"
    elif action == "rm-pattern":
        result = filters.remove_pattern(value or "")
        return f"skip_patterns: {', '.join(result) or '(empty)'}"
    elif action == "skip-groups":
        val = (value or "").lower() in ("true", "yes", "on", "1")
        result = filters.set_skip_groups(val)
        return f"skip_groups: {result}"
    elif action == "reset":
        filters.reset()
        return "Filters reset to defaults."
    else:  # "show" or default
        config = filters.get_config()
        lines = [
            f"skip_senders:  {', '.join(config['skip_senders']) or '(none)'}",
            f"skip_domains:  {', '.join(config['skip_domains']) or '(none)'}",
            f"skip_groups:   {config['skip_groups']}",
            f"skip_patterns: {', '.join(config['skip_patterns']) or '(none)'}",
        ]
        return "\n".join(lines)


async def preload(
    source: str,
    query: str | None = None,
    contact: str | None = None,
    since: str | None = None,
    max_pages: int = 100,
    page_size: int = 50,
    fetch_bodies: bool = False,
    resume_job: str | None = None,
    throttle: float = 0.2,
) -> str:
    """Paginate through message history, caching results page by page.

    Returns a summary string describing what was cached.
    """
    all_cfg = _ensure_sources()
    prefixes = _resolve_prefixes(source)
    if not prefixes:
        return "Error: no source configured for {!r}.".format(source)
    prefix = prefixes[0]
    cfg = all_cfg.get(prefix)
    if not cfg:
        return f"Error: source {prefix!r} not found."

    provider = cfg.get("provider", "").lower()

    # Contact auto-expand
    if contact:
        resolved_query = _contact_to_query(contact, prefix, provider)
        if resolved_query is None:
            return f"Error: no {prefix}: identifiers found for contact {contact!r}."
        query = resolved_query

    # Since → query fragment
    if since and not query:
        if provider == "gmail":
            query = _since_to_gmail_query(since, prefix)
        elif provider == "o365":
            query = since  # O365 will use $search
    elif since and query:
        if provider == "gmail":
            query = f"{query} {_since_to_gmail_query(since, prefix)}"

    effective_query = query or ("in:inbox" if provider == "gmail" else "")

    # Resume or create job
    if resume_job:
        job = batch.get_job(resume_job)
        if job is None:
            return f"Error: job {resume_job!r} not found."
        if job["status"] not in ("in_progress", "failed", "stopped_disk_low"):
            return f"Error: job {resume_job!r} has status {job['status']!r} — cannot resume."
        job_id = resume_job
        page_token = job.get("next_page_token")
        pages_fetched = job.get("pages_fetched", 0)
        messages_cached = job.get("messages_cached", 0)
        batch.update_job(job_id, status="in_progress", error=None)
    else:
        if not cache.check_disk_space():
            return "Error: less than 5 GB free disk space. Free space and retry."
        job_id = batch.create_job(prefix, effective_query)
        page_token = None
        pages_fetched = 0
        messages_cached = 0

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        batch.update_job(job_id, status="failed", error="adapter not available")
        return f"Error: source {prefix!r} not available."

    try:
        async with adapter:
            while pages_fetched < max_pages:
                # Disk check every 10 pages
                if pages_fetched > 0 and pages_fetched % 10 == 0:
                    if not cache.check_disk_space():
                        batch.update_job(
                            job_id,
                            status="stopped_disk_low",
                            pages_fetched=pages_fetched,
                            messages_cached=messages_cached,
                            next_page_token=page_token,
                        )
                        return (
                            f"Preload stopped — low disk space. "
                            f"{pages_fetched} pages, {messages_cached} messages cached. "
                            f"Resume: ts4k preload --resume {job_id}"
                        )

                listing = await adapter.list_messages(
                    query=effective_query, count=page_size, page_token=page_token
                )

                if not listing:
                    break

                # Cache each entry
                for entry in listing:
                    msg_id = entry.get("id", "")
                    if not msg_id:
                        continue
                    cache.store_header(msg_id, entry)

                    if fetch_bodies:
                        try:
                            msg = await adapter.read_message(msg_id)
                            msg = _normalize_message(msg)
                            cache.store_message(msg_id, msg)
                        except Exception as exc:
                            logger.warning("[%s] body fetch %s: %s", prefix, msg_id, exc)

                    messages_cached += 1

                pages_fetched += 1

                # Extract next page token
                next_token = listing[-1].get("_next_page_token")
                page_token = next_token

                # Checkpoint
                batch.update_job(
                    job_id,
                    pages_fetched=pages_fetched,
                    messages_cached=messages_cached,
                    next_page_token=page_token,
                )

                if not next_token:
                    break

                # Throttle
                if throttle > 0:
                    await asyncio.sleep(throttle)

    except Exception as exc:
        batch.update_job(
            job_id,
            status="failed",
            error=str(exc),
            pages_fetched=pages_fetched,
            messages_cached=messages_cached,
            next_page_token=page_token,
        )
        return (
            f"Preload failed: {exc}\n"
            f"{pages_fetched} pages, {messages_cached} messages cached. "
            f"Resume: ts4k preload --resume {job_id}"
        )

    batch.update_job(job_id, status="done")
    return (
        f"Preload complete: {pages_fetched} pages, {messages_cached} messages cached "
        f"(job {job_id})."
    )


def _contact_to_query(
    contact: str, prefix: str, provider: str
) -> str | None:
    """Expand a contact alias into a platform-specific bidirectional query."""
    idents = contacts.query(contact.lower().strip())
    if not idents:
        # Try find (substring match)
        matches = contacts.find(contact.lower().strip())
        if matches:
            # Use first match
            idents = next(iter(matches.values()))
        else:
            return None

    # Filter by source prefix
    matching = [i for i in idents if i.startswith(f"{prefix}:")]
    if not matching:
        return None

    # Strip prefix
    raw_ids = [i[len(prefix) + 1 :] for i in matching]

    if provider == "gmail":
        # Gmail: {from:addr OR to:addr}
        clauses = []
        for addr in raw_ids:
            clauses.append(f"from:{addr}")
            clauses.append(f"to:{addr}")
        return "{" + " OR ".join(clauses) + "}"

    if provider == "o365":
        # O365: quoted search across all fields
        return " OR ".join(f'"{addr}"' for addr in raw_ids)

    return None


def manage_preload(action: str, job_id: str | None = None) -> str:
    """Manage preload jobs: status or cancel."""
    if action == "status":
        jobs = batch.list_jobs()
        if not jobs:
            return "No preload jobs."
        lines = []
        for jid, job in sorted(jobs.items(), key=lambda x: x[1].get("started_at", "")):
            status = job.get("status", "?")
            pages = job.get("pages_fetched", 0)
            msgs = job.get("messages_cached", 0)
            src = job.get("source", "?")
            query = job.get("query", "")
            error = job.get("error")
            line = f"{jid}|{src}|{status}|{pages} pages|{msgs} msgs|{query}"
            if error:
                line += f"|err: {error}"
            lines.append(line)
        return "\n".join(lines)

    if action == "cancel":
        if not job_id:
            return "Error: job_id required for cancel."
        job = batch.get_job(job_id)
        if job is None:
            return f"Error: job {job_id!r} not found."
        if job["status"] == "in_progress":
            batch.update_job(job_id, status="failed", error="cancelled by user")
        return f"Job {job_id} cancelled."

    return f"Unknown preload action: {action!r}"


def manage_cache(
    action: str | None = None,
    source: str | None = None,
    stale_only: bool = False,
) -> str:
    """Manage message cache. Returns output string."""
    if action == "clear":
        removed = cache.clear(source=source, stale_only=stale_only)
        scope = f"source {source}" if source else "all sources"
        qualifier = " stale" if stale_only else ""
        return f"Cleared {removed}{qualifier} cached messages ({scope})."

    # Default: stats
    s = cache.stats()
    lines = [f"Cache: {s['total']} messages, {s['bodies']} bodies"]
    if s["stale"]:
        lines[0] += f" ({s['stale']} stale)"

    if s["by_source"]:
        for src, n in sorted(s["by_source"].items()):
            label = {"g": "Gmail", "o": "O365"}.get(src, src)
            lines.append(f"  {label}: {n} messages")

    lines.append(f"  Index: {estimate_size(s['index_bytes'])}")
    lines.append(f"  Bodies: {estimate_size(s['bodies_bytes'])}")

    if s["oldest"]:
        lines.append(f"  Range: {s['oldest'][:10]} .. {s['newest'][:10]}")

    lines.append(f"  Schema: v{s['schema_version']}")
    return "\n".join(lines)

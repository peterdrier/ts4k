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
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ts4k.adapters.caldav_cal import CaldavAdapter, CaldavAdapterConfig
from ts4k.adapters.gcal import GcalAdapter, GcalAdapterConfig
from ts4k.adapters.o365cal import O365CalAdapter, O365CalAdapterConfig
from ts4k.adapters.github import GitHubAdapter, GitHubAdapterConfig
from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
from ts4k.adapters.http import HTTPAdapter, HTTPAdapterConfig
from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig
from ts4k.adapters import wa_bridge_auth
from ts4k.adapters.whatsapp import WhatsAppAdapter, WhatsAppAdapterConfig
from ts4k.core.filter import apply_filters
from ts4k.core.format import (
    collapse_threads,
    estimate_size,
    format_event_detail,
    format_events,
    format_listing,
    format_mailbox_stats,
    format_message,
    format_overview,
    format_thread,
    format_thread_listing,
)
from ts4k.core.normalize import normalize, normalize_headers
from ts4k.core.tz import display_tzinfo, parse_utc
from ts4k.state import batch, cache, contacts, filters, media, meters, sources, stats
from ts4k.state.refs import RefTable

if TYPE_CHECKING:
    from datetime import tzinfo

    from ts4k.auth.health import TokenHealth

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
    ref_map: dict[str, int] | None = None  # {full_id: ref_num}
    has_more: bool = False
    remaining: int = 0
    _messages: list[dict] | None = None  # internal: raw messages for watermark tracking
    _truncated_sources: set[str] | None = None  # internal: sources with messages dropped by count
    _continuation_hint: str = ""  # internal: actionable "get more" command


# ---------------------------------------------------------------------------
# Source config resolution
# ---------------------------------------------------------------------------


def _ensure_sources() -> dict[str, dict[str, Any]]:
    """Load source config from sources.json."""
    return sources.list_all()


_CAL_PROVIDERS = ("gcal", "o365cal", "caldav")

# Providers that carry no messages — excluded from message stats.
_NON_MESSAGE_PROVIDERS = (*_CAL_PROVIDERS, "carddav")


def _make_adapter(
    prefix: str, cfg: dict[str, Any]
) -> GmailAdapter | WhatsAppAdapter | O365Adapter | GitHubAdapter | HTTPAdapter | GcalAdapter | O365CalAdapter | CaldavAdapter | None:
    """Create an adapter instance from a source config entry."""
    provider = cfg.get("provider", "").lower()

    if provider == "gmail":
        email = cfg.get("email")
        if not email:
            return None
        config_dir = cfg.get("config_dir")
        return GmailAdapter(
            GmailAdapterConfig(
                user_email=email,
                config_dir=Path(config_dir) if config_dir else None,
                level=cfg.get("level"),
            ),
            prefix=prefix,
        )

    if provider == "whatsapp":
        # "stdio" spawns the Python whatsapp-mcp-server as a subprocess. It is
        # the documented rollback from the HTTP transport (ts4k#71) and stays
        # available while that server exists (whatsapp-mcp#85).
        if (cfg.get("transport") or "http").lower() == "stdio":
            cwd = cfg.get("mcp_cwd", "")
            if not cwd or not os.path.isdir(cwd):
                return None
            cmd = cfg.get("server_command", ["uv", "run", "main.py"])
            if isinstance(cmd, str):
                cmd = cmd.split()
            return WhatsAppAdapter(
                WhatsAppAdapterConfig(
                    transport="stdio", server_command=cmd, server_cwd=cwd
                ),
                prefix=prefix,
            )
        return WhatsAppAdapter(
            WhatsAppAdapterConfig(
                transport="http",
                bridge_url=cfg.get("bridge_url") or wa_bridge_auth.DEFAULT_BRIDGE_URL,
                bridge_token=cfg.get("bridge_token"),
                bridge_token_file=cfg.get("bridge_token_file"),
            ),
            prefix=prefix,
        )

    if provider == "o365":
        client_id = cfg.get("client_id", "")
        if not client_id:
            logger.warning("O365 source %r missing client_id", prefix)
            return None
        return O365Adapter(
            O365AdapterConfig(
                client_id=client_id,
                tenant_id=cfg.get("tenant_id", "common"),
                mailbox=cfg.get("mailbox"),
                config_dir=Path(cfg["config_dir"]) if cfg.get("config_dir") else None,
                level=cfg.get("level"),
            ),
            prefix=prefix,
        )

    if provider == "github":
        from ts4k.adapters.github import resolve_token
        if not resolve_token(cfg.get("token"), cfg.get("token_file")):
            logger.warning("GitHub source %r has no resolvable token", prefix)
            return None
        return GitHubAdapter(
            GitHubAdapterConfig(
                token=cfg.get("token", ""),
                token_file=cfg.get("token_file"),
                level=cfg.get("level"),
            ),
            prefix=prefix,
        )

    if provider == "http":
        url = cfg.get("url", "")
        if not url:
            logger.warning("HTTP source %r missing url", prefix)
            return None
        return HTTPAdapter(
            HTTPAdapterConfig(url=url, header=cfg.get("header")),
            prefix=prefix,
        )

    if provider == "gcal":
        email = cfg.get("email")
        calendar_id = cfg.get("calendar_id")
        if not email or not calendar_id:
            return None
        config = GcalAdapterConfig(
            email=email,
            calendar_id=calendar_id,
            calendar_name=cfg.get("calendar_name", calendar_id),
            timezone=cfg.get("timezone", "UTC"),
            config_dir=Path(cfg["config_dir"]) if cfg.get("config_dir") else None,
            level=cfg.get("level", "readonly"),
        )
        return GcalAdapter(config, prefix=prefix)

    if provider == "o365cal":
        email = cfg.get("email")
        client_id = cfg.get("client_id", "")
        if not email or not client_id:
            return None
        config = O365CalAdapterConfig(
            email=email,
            client_id=client_id,
            tenant_id=cfg.get("tenant_id", "common"),
            calendar_id=cfg.get("calendar_id", "default"),
            calendar_name=cfg.get("calendar_name", ""),
            timezone=cfg.get("timezone", "UTC"),
            config_dir=Path(cfg["config_dir"]) if cfg.get("config_dir") else None,
            level=cfg.get("level", "readonly"),
        )
        return O365CalAdapter(config, prefix=prefix)

    if provider == "caldav":
        email = cfg.get("email")
        calendar_id = cfg.get("calendar_id")
        if not email or not calendar_id:
            return None
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL
        config = CaldavAdapterConfig(
            email=email,
            server_url=cfg.get("server_url", ICLOUD_CALDAV_URL),
            calendar_id=calendar_id,
            calendar_name=cfg.get("calendar_name", ""),
            timezone=cfg.get("timezone", "UTC"),
            config_dir=Path(cfg["config_dir"]) if cfg.get("config_dir") else None,
            level=cfg.get("level", "readonly"),
        )
        return CaldavAdapter(config, prefix=prefix)

    if provider == "carddav":
        # Contacts are not messages: CardDAV sources are reached only by
        # `ts4k contacts sync`, never by the message fan-out.
        return None

    logger.warning("Unknown provider %r for source %r", provider, prefix)
    return None


def _resolve_prefixes(source: str | None) -> list[str]:
    """Determine which source prefixes to query.

    Accepts a specific prefix, a provider name, or ``"all"`` (default).
    """
    raw = (source or "all").strip()
    all_cfg = _ensure_sources()

    if raw.lower() == "all":
        return list(all_cfg.keys())

    # Prefixes keep the case they were added with — exact key wins
    if raw in all_cfg:
        return [raw]

    source = raw.lower()
    if source in all_cfg:
        return [source]

    provider_map = {
        "wa": "whatsapp", "outlook": "o365", "office": "o365", "365": "o365",
        "gh": "github",
        "google-calendar": "gcal", "calendar": "gcal", "cal": "gcal",
        "o365-calendar": "o365cal", "outlook-calendar": "o365cal",
        "apple": "caldav", "icloud": "caldav", "apple-calendar": "caldav",
    }
    provider = provider_map.get(source, source)
    matches = [
        p for p, c in all_cfg.items() if c.get("provider", "").lower() == provider
    ]
    if matches:
        return matches

    return [source]


_ACTIVITY_ACTIVE_DAYS = 30


def source_activity(
    prefix: str, provider: str = "", headers: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Cache-derived activity metadata for a source. Local cache only, no network calls.

    Returns a dict with ``count`` (cached message count), ``newest`` (ISO date
    of the newest cached message, or ``None``), and ``tag``:

    - ``"active"``: newest cached message within the last 30 days
    - ``"low"``: cached messages exist, but the newest is older than 30 days
    - ``"empty"``: no cached messages (nothing fetched yet, or genuinely idle)
    - ``"n/a"``: provider isn't cached locally (WhatsApp, calendars) — see
      ``cache.CACHEABLE_PROVIDERS``

    *headers*, if given, is a pre-loaded list of this source's cached headers
    (see ``cached_headers_by_source``) — pass it to avoid reloading the whole
    cache index per call. ``None`` (default) loads just this source's headers.

    Note: this reflects what ts4k has *cached* so far, not a live mailbox
    count — a source that's never been queried also reports "empty".
    """
    if provider and provider.lower() not in cache.CACHEABLE_PROVIDERS:
        return {"count": 0, "newest": None, "tag": "n/a"}

    if headers is None:
        headers = cache.list_headers(source=prefix)
    if not headers:
        return {"count": 0, "newest": None, "tag": "empty"}

    dates = [h.get("date", "") for h in headers if h.get("date")]
    newest = max(dates) if dates else None
    if newest:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_ACTIVITY_ACTIVE_DAYS)) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        tag = "active" if newest >= cutoff else "low"
    else:
        tag = "low"
    return {"count": len(headers), "newest": newest, "tag": tag}


def cached_headers_by_source() -> dict[str, list[dict[str, Any]]]:
    """Load the entire cache index once and group headers by source prefix.

    Pass the result's per-prefix lists to ``source_activity(..., headers=...)``
    when computing activity for every configured source, so callers don't
    reload and rescan the full cache index once per source.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for h in cache.list_headers():
        groups.setdefault(h.get("source", ""), []).append(h)
    return groups


def _prefix_from_id(prefixed_id: str, all_cfg: dict[str, dict[str, Any]]) -> str:
    """Extract source prefix from a prefixed ID like ``'g:xxx'``."""
    for prefix in sorted(all_cfg.keys(), key=len, reverse=True):
        if prefixed_id.startswith(f"{prefix}:"):
            return prefix
    if ":" in prefixed_id:
        return prefixed_id.split(":")[0]
    # No colon found — likely wrong syntax (e.g. 'ts4k g inbox')
    known = ", ".join(sorted(all_cfg.keys())) if all_cfg else "none configured"
    raise ValueError(
        f"Invalid message ID {prefixed_id!r} — expected format like 'g:abc123'.\n"
        f"  Configured sources: {known}\n"
        f"  Did you mean: ts4k list --source {prefixed_id}?"
    )


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------


def _normalize_message(msg: dict, mode: str = "compact") -> dict:
    """Run the normalizer on a message dict (body + headers)."""
    result = dict(msg)

    if result.get("body"):
        result["body"] = normalize(result["body"], mode=mode)

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
        return "newer_than:1d"

    if len(since) >= 2 and since[-1] in ("d", "h") and since[:-1].isdigit():
        return f"newer_than:{since}"

    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return f"after:{int(dt.timestamp())}"
    except ValueError:
        pass

    return since


def _since_to_iso(since: str | None, prefix: str) -> str | None:
    """Convert a --since value to an ISO timestamp for adapters that take ISO."""
    if since is None:
        return None

    # Support relative time: Nd (days) and Nh (hours)
    if len(since) >= 2 and since[-1] in ("d", "h") and since[:-1].isdigit():
        n = int(since[:-1])
        delta = timedelta(days=n) if since[-1] == "d" else timedelta(hours=n)
        dt = datetime.now(timezone.utc) - delta
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return since


def _utc_to_gmail_query(since: str | None) -> str:
    """Convert a UTC ISO timestamp to a Gmail ``after:EPOCH`` query fragment.

    Returns ``"newer_than:1d"`` if *since* is None.
    """
    if since is None:
        return "newer_than:1d"
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return f"after:{int(dt.timestamp())}"
    except ValueError:
        return "newer_than:1d"


def _resolve_since_to_utc(since: str | None) -> str | None:
    """Convert a relative time or ISO string to a UTC ISO timestamp.

    Accepts ``"2d"``, ``"6h"``, an ISO timestamp, or ``"all"`` (returns None).
    Returns None if *since* is None (caller decides default).
    """
    if since is None:
        return None
    if since.lower() == "all":
        return None
    # Relative: Nd, Nh, or Nw
    if len(since) >= 2 and since[-1] in ("d", "h", "w") and since[:-1].isdigit():
        n = int(since[:-1])
        unit = since[-1]
        if unit == "w":
            delta = timedelta(weeks=n)
        elif unit == "d":
            delta = timedelta(days=n)
        else:
            delta = timedelta(hours=n)
        dt = datetime.now(timezone.utc) - delta
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Assume ISO already
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
# Ref resolution
# ---------------------------------------------------------------------------


def _resolve_ref(id_or_ref: str, ref_table: RefTable | None) -> str:
    """Translate ``N`` or ``#N`` to a real ID via *ref_table*, or pass through."""
    if ref_table is not None and (id_or_ref.startswith("#") or id_or_ref.isdigit()):
        resolved = ref_table.resolve(id_or_ref)
        if resolved is not None:
            return resolved
    return id_or_ref


# ---------------------------------------------------------------------------
# Parallel adapter helpers
# ---------------------------------------------------------------------------


def _matches_post_filters(
    msg: dict, query: str | None = None,
    sender: str | None = None, domain: str | None = None,
) -> bool:
    """Client-side backstop so listing filters always stack (ts4k#105).

    Adapters apply *sender*/*domain*/*query* server-side where they can, but
    support is adapter-specific — WhatsApp's whatsnew ignores all three, so
    without this backstop those filters silently vanish on the time-aware
    fetch path.  ``from`` is normalized to a bare email address for mail
    sources, so *sender*/*domain* naturally exclude sources whose senders
    have no email address (e.g. WhatsApp chat names) rather than matching
    vacuously.
    """
    frm = msg.get("from", "").lower()
    if sender and sender.lower() not in frm:
        return False
    if domain:
        # Require an actual email address — a plain sender name that merely
        # ends in ".domain" (e.g. an HTTP source named "alerts.example.com")
        # must not match. Compare only the address's domain component.
        if "@" not in frm:
            return False
        d = domain.lower().lstrip("@")
        addr_domain = frm.rsplit("@", 1)[1].strip("> ")
        if not (addr_domain == d or addr_domain.endswith("." + d)):
            return False
    if query:
        q = query.lower()
        if not any(
            q in str(msg.get(k, "")).lower()
            for k in ("from", "subject", "snippet")
        ):
            return False
    return True


async def _fetch_for_source(
    prefix: str, cfg: dict[str, Any], since: str | None, count: int,
    sender: str | None = None, domain: str | None = None,
    query: str | None = None,
) -> list[dict]:
    """Fetch new messages from a single source.

    *since* must be a resolved UTC ISO timestamp (or None for no bound).
    Returns normalized message dicts.
    """
    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return []

    provider = cfg.get("provider", "").lower()
    try:
        async with adapter:
            # Over-fetch past count so the aggregation layer can see
            # truncation (has_more / watermark direction). Everything
            # fetched is returned; _fetch_messages truncates to count.
            github_search = provider == "github" and query is not None
            if provider == "gmail":
                gmail_query = _utc_to_gmail_query(since)
                if query:
                    # Gmail search terms AND together, so the user query
                    # rides the same server-side search as the time bound.
                    gmail_query = f"{query} {gmail_query}"
                listing = await adapter.list_messages(
                    query=gmail_query, count=count + 1,
                    sender=sender, domain=domain,
                )
            elif github_search:
                # GitHub defines query as GitHub search syntax and has no
                # query support in whatsnew — substring-matching it against
                # headers would reject every valid search. Search natively;
                # the time bound is applied client-side below.
                listing = await adapter.list_messages(
                    query=query, count=count + 1, sender=sender, domain=domain,
                )
            else:
                listing = await adapter.whatsnew(
                    since=since, sender=sender, domain=domain,
                    count=max(count + 1, 200),
                )

            if not listing:
                return []
            messages = []
            for entry in listing:
                msg = _normalize_message(entry)
                msg.setdefault("source", prefix)
                # WhatsApp entries carry their preview text in `body` (no
                # separate snippet fetch, unlike Gmail/O365's snippet-only
                # listing calls) — without this, format_listing has nothing
                # to show and WhatsApp rows render with no preview at all.
                msg.setdefault("snippet", msg.get("body", ""))
                cache.store_message(msg.get("id", ""), msg, provider=provider)
                messages.append(msg)
            # Gmail and GitHub matched *query* natively (full-text / search
            # syntax invisible to the backstop) — re-filtering it here would
            # drop legitimate matches.
            native_query = provider == "gmail" or github_search
            results = []
            for m in messages:
                if github_search and since and m.get("date", "") < since:
                    continue
                if not _matches_post_filters(
                    m, query=None if native_query else query,
                    sender=sender, domain=domain,
                ):
                    continue
                results.append(m)
            return results

    except Exception as exc:
        logger.warning("[%s] adapter failed: %s", prefix, exc)
        return []


def _format_rows(
    messages: list[dict],
    fmt: str,
    ref_table: RefTable | None,
    threads: bool,
) -> tuple[str, dict[str, int] | None]:
    """Format a listing, collapsing to one row per thread when *threads*.

    Returns ``(output, ref_map)``.  In thread mode the refs are assigned to
    the collapsed rows, so they resolve to threads rather than messages.
    """
    rows = collapse_threads(messages) if threads else messages
    ref_map = ref_table.assign(rows) if ref_table else None
    if threads:
        return format_thread_listing(rows, fmt=fmt, ref_map=ref_map), ref_map
    return format_listing(rows, fmt=fmt, ref_map=ref_map), ref_map


async def _fetch_messages(
    since: dict[str, str | None],
    count: int = 20,
    source: str | None = None,
    fmt: str = "pipe",
    filter: bool = False,
    ref_table: RefTable | None = None,
    stat_cmd: str = "wn",
    sender: str | None = None,
    domain: str | None = None,
    query: str | None = None,
    threads: bool = False,
) -> CommandResult:
    """Shared fetch layer — parallel fetch, collate, format.

    Args:
        since: Mapping of source prefix to resolved UTC ISO timestamp
               (or None for default/no bound).
        count: Maximum messages to return.
        source: Original source arg (for error messages).
        fmt: Output format (pipe, json, xml).
        filter: Whether to apply skip filters.
        ref_table: Optional ref table for short refs.
        stat_cmd: Stats command label (``"wn"`` or ``"l"``).
        query: Free-text filter — server-side for Gmail, client-side
               backstop for the rest (see ``_matches_post_filters``).
        threads: Collapse the fetched messages into one row per thread.

    Returns a CommandResult with ``_messages`` populated (the truncated list).
    Does NOT touch watermarks.
    """
    all_cfg = _ensure_sources()
    active_prefixes = list(since.keys())

    tasks: list[asyncio.Task] = []
    task_prefixes: list[str] = []

    for prefix in active_prefixes:
        cfg = all_cfg.get(prefix)
        if cfg:
            tasks.append(
                asyncio.create_task(
                    _fetch_for_source(prefix, cfg, since[prefix], count,
                                     sender=sender, domain=domain, query=query)
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

    if filter:
        all_messages = apply_filters(all_messages, filters.get_config())

    total_available = len(all_messages)
    truncated = all_messages[:count]
    has_more = total_available > count
    remaining = total_available - count if has_more else 0

    # Track which sources had messages dropped by count truncation
    truncated_sources: set[str] = set()
    if has_more:
        for m in all_messages[count:]:
            src = m.get("source", "")
            if src:
                truncated_sources.add(src)

    if not truncated:
        return CommandResult(error="No new messages.")

    output, ref_map = _format_rows(truncated, fmt, ref_table, threads)

    if has_more:
        output += f"\n--- {remaining} more messages available ---"

    # Build continuation hint for paginating older results
    continuation_hint = ""
    if has_more and truncated:
        oldest_date = truncated[-1].get("date", "")[:10]  # YYYY-MM-DD
        if oldest_date:
            cmd_name = "list"
            parts = [f"ts4k {cmd_name}"]
            if source and source != "all":
                parts.append(f"--source {source}")
            parts.append(f"--since {oldest_date}")
            parts.append(f"-n {count}")
            if query:
                parts.append(f"-q {shlex.quote(query)}")
            if sender:
                parts.append(f"--from {sender}")
            if domain:
                parts.append(f"--domain {domain}")
            if threads:
                parts.append("--threads")
            continuation_hint = " ".join(parts)

    _record_stats(stat_cmd, truncated, output)

    return CommandResult(
        output=output,
        messages_processed=len(truncated),
        ref_map=ref_map,
        has_more=has_more,
        remaining=remaining,
        _messages=truncated,
        _truncated_sources=truncated_sources or None,
        _continuation_hint=continuation_hint,
    )


# ---------------------------------------------------------------------------
# Command functions
# ---------------------------------------------------------------------------


async def updates(
    source: str | None = None,
    since: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    filter: bool = False,
    ref_table: RefTable | None = None,
    sender: str | None = None,
    domain: str | None = None,
    threads: bool = False,
) -> CommandResult:
    """Deprecated — use list_messages(since=...) instead."""
    return await list_messages(
        source=source,
        since=since,
        count=count,
        fmt=fmt,
        filter=filter,
        ref_table=ref_table,
        sender=sender,
        domain=domain,
        threads=threads,
    )


async def whatsnew(
    key: str,
    source: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    filter: bool = False,
    ref_table: RefTable | None = None,
    sender: str | None = None,
    domain: str | None = None,
    threads: bool = False,
) -> CommandResult:
    """Fetch new messages using keyed watermarks."""
    from ts4k.state import keyed_watermarks

    active_prefixes = _resolve_prefixes(source)
    saved = keyed_watermarks.get_all(key)

    default_since = (
        datetime.now(timezone.utc) - timedelta(days=7)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    since_map = {p: saved.get(p, default_since) for p in active_prefixes}

    result = await _fetch_messages(
        since=since_map,
        count=count,
        source=source,
        fmt=fmt,
        filter=filter,
        ref_table=ref_table,
        stat_cmd="wn",
        sender=sender,
        domain=domain,
        threads=threads,
    )

    # Advance watermarks per source.
    # Sources that had messages dropped by count truncation advance to the
    # OLDEST returned message (some overlap, no permanent loss).  Sources
    # that were fully returned advance to the NEWEST (standard behaviour).
    if result._messages:
        truncated_srcs = result._truncated_sources or set()
        oldest: dict[str, str] = {}
        newest: dict[str, str] = {}
        for msg in result._messages:
            src = msg.get("source", "")
            date = msg.get("date", "")
            if not src or not date:
                continue
            if src not in oldest or date < oldest[src]:
                oldest[src] = date
            if date > newest.get(src, ""):
                newest[src] = date

        new_watermarks: dict[str, str] = {}
        for src in {*oldest, *newest}:
            if src in truncated_srcs:
                new_watermarks[src] = oldest[src]
            else:
                new_watermarks[src] = newest[src]

        if new_watermarks:
            keyed_watermarks.update(key, new_watermarks)

    return result


async def get_message(
    id: str, fmt: str = "pipe", ref_table: RefTable | None = None,
    body_mode: str = "compact",
) -> CommandResult:
    """Read a single message by prefixed ID or short ref (``#3``).

    ``body_mode`` selects normalization style: ``"compact"`` (default,
    token-efficient) or ``"readable"`` (preserves paragraph breaks,
    markdown emphasis, and real markdown tables for human display).
    """
    id = _resolve_ref(id, ref_table)

    # Read-through: check cache first. The cache stores the compact-mode
    # body, so only serve it when compact mode is requested.
    if body_mode == "compact":
        cached = cache.get_message(id)
        if cached and cached.get("body"):
            output = format_message(cached, fmt=fmt)
            _record_stats("g", [cached], output)
            return CommandResult(output=output, messages_processed=1)

    all_cfg = _ensure_sources()
    try:
        prefix = _prefix_from_id(id, all_cfg)
    except ValueError as exc:
        return CommandResult(error=str(exc))
    cfg = all_cfg.get(prefix)

    if not cfg:
        return CommandResult(error=f"No source configured for prefix {prefix!r}.")

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return CommandResult(error=f"Source {prefix!r} not available.")

    provider = cfg.get("provider", "").lower()
    async with adapter:
        msg = await adapter.read_message(id, prefer_html=(body_mode == "readable"))
        msg = _normalize_message(msg, mode=body_mode)
        if body_mode == "readable" and not (msg.get("body") or "").strip():
            # The preferred HTML alternative normalized to nothing (e.g. an
            # empty shell or tracking-only markup) — retry once with the
            # plain-text alternative instead of returning an empty body.
            plain_msg = await adapter.read_message(id, prefer_html=False)
            plain_msg = _normalize_message(plain_msg, mode=body_mode)
            if (plain_msg.get("body") or "").strip():
                msg = plain_msg
        if body_mode == "compact":
            cache.store_message(id, msg, provider=provider)
        output = format_message(msg, fmt=fmt)
        _record_stats("g", [msg], output)

    return CommandResult(output=output, messages_processed=1)


async def get_media(id: str, ref_table: RefTable | None = None) -> CommandResult:
    """Download the media file attached to a message and return its local path.

    Delegates to the adapter's ``download_media()`` (WhatsApp-only for now;
    other adapters raise ``NotImplementedError``) then relocates whatever
    it staged into ts4k's own media store under ``~/.config/ts4k/media/``,
    so callers always get a stable, ts4k-owned path. Nothing here assumes
    WhatsApp — a future Gmail/O365 attachment adapter can implement
    ``download_media()`` and this command picks it up unchanged.
    """
    id = _resolve_ref(id, ref_table)
    all_cfg = _ensure_sources()
    try:
        prefix = _prefix_from_id(id, all_cfg)
    except ValueError as exc:
        return CommandResult(error=str(exc))
    cfg = all_cfg.get(prefix)

    if not cfg:
        return CommandResult(error=f"No source configured for prefix {prefix!r}.")

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return CommandResult(error=f"Source {prefix!r} not available.")

    async with adapter:
        try:
            result = await adapter.download_media(id)
        except NotImplementedError:
            return CommandResult(error=f"Source {prefix!r} does not support media download.")

    if not result.get("success"):
        return CommandResult(error=result.get("message") or "Failed to download media.")

    src_path = result.get("file_path")
    if not src_path:
        return CommandResult(error="Bridge reported success but returned no file path.")

    dest = media.save_media(src_path, id)
    return CommandResult(output=str(dest))


async def get_thread(
    tid: str, fmt: str = "pipe", ref_table: RefTable | None = None
) -> CommandResult:
    """Read a thread/conversation by prefixed ID or short ref (``#3``)."""
    tid = _resolve_ref(tid, ref_table)
    all_cfg = _ensure_sources()
    try:
        prefix = _prefix_from_id(tid, all_cfg)
    except ValueError as exc:
        return CommandResult(error=str(exc))
    cfg = all_cfg.get(prefix)

    if not cfg:
        return CommandResult(error=f"No source configured for prefix {prefix!r}.")

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return CommandResult(error=f"Source {prefix!r} not available.")

    provider = cfg.get("provider", "").lower()

    # If the ID looks like a message ID (not a thread ID), try resolving
    # via cache — the cached message may have a thread_id field.
    cached_msg = cache.get_message(tid)
    if cached_msg and cached_msg.get("thread_id"):
        tid = cached_msg["thread_id"]

    async with adapter:
        thread = await adapter.read_thread(tid)
        thread = _normalize_thread(thread)
        for msg in thread.get("messages", []):
            mid = msg.get("id")
            if mid:
                cache.store_message(mid, msg, provider=provider)
        output = format_thread(thread, fmt=fmt)
        _record_stats("t", thread.get("messages", []), output)

    return CommandResult(output=output, messages_processed=len(thread.get("messages", [])))


async def list_messages(
    source: str | None = None,
    query: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    filter: bool = False,
    ref_table: RefTable | None = None,
    sender: str | None = None,
    domain: str | None = None,
    since: str | None = None,
    threads: bool = False,
) -> CommandResult:
    """List messages matching filters. All filters stack.

    When *since* is provided, uses the time-aware fetch path (adapter.whatsnew
    for O365, after: query for Gmail).  Otherwise uses adapter.list_messages.

    When *threads* is set the result collapses to one row per thread, and the
    assigned refs resolve to threads.
    """
    if since is not None:
        # Time-aware path — delegate to _fetch_messages which handles
        # per-provider since conversion and parallel fetch.
        active_prefixes = _resolve_prefixes(source)
        resolved_ts = _resolve_since_to_utc(since)
        since_map = {p: resolved_ts for p in active_prefixes}
        return await _fetch_messages(
            since=since_map,
            count=count,
            source=source,
            fmt=fmt,
            filter=filter,
            ref_table=ref_table,
            stat_cmd="l",
            sender=sender,
            domain=domain,
            query=query,
            threads=threads,
        )

    # Query-only path — no time filter.
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
                listing = await adapter.list_messages(query=query, count=count, sender=sender, domain=domain)
                for entry in listing or []:
                    msg = _normalize_message(entry)
                    msg.setdefault("source", prefix)
                    msg.setdefault("snippet", msg.get("body", ""))
                    cache.store_message(msg.get("id", ""), msg, provider=provider)
                    all_messages.append(msg)
        except Exception as exc:
            logger.warning("[%s] adapter failed: %s", prefix, exc)

    if not all_messages:
        return CommandResult(error="No messages found.")

    all_messages.sort(key=lambda m: m.get("date", ""), reverse=True)

    if filter:
        all_messages = apply_filters(all_messages, filters.get_config())

    all_messages = all_messages[:count]

    if not all_messages:
        return CommandResult(error="No messages found.")

    output, ref_map = _format_rows(all_messages, fmt, ref_table, threads)
    _record_stats("l", all_messages, output)

    return CommandResult(
        output=output, messages_processed=len(all_messages), ref_map=ref_map
    )


async def get_mailbox_stats(source: str | None = None) -> dict[str, dict | None]:
    """Fetch live mailbox stats from email adapters.

    Returns ``{prefix: stats_dict | None}`` — ``None`` means unreachable.
    Fetches from all sources concurrently.
    """
    all_cfg = _ensure_sources()
    prefixes = _resolve_prefixes(source)

    async def _fetch_one(prefix: str) -> tuple[str, dict | None]:
        cfg = all_cfg.get(prefix)
        if not cfg:
            return prefix, None
        provider = cfg.get("provider", "").lower()
        if provider not in ("gmail", "o365"):
            return prefix, None  # only email adapters support mailbox_stats

        adapter = _make_adapter(prefix, cfg)
        if adapter is None:
            return prefix, None

        try:
            await adapter.connect()
            stats_data = await asyncio.wait_for(
                adapter.mailbox_stats(), timeout=10
            )
            return prefix, stats_data
        except Exception as exc:
            logger.warning("mailbox_stats failed for %s: %s", prefix, exc)
            return prefix, None
        finally:
            try:
                await adapter.disconnect()
            except Exception:
                pass

    email_prefixes = [
        p for p in prefixes
        if all_cfg.get(p, {}).get("provider", "").lower() in ("gmail", "o365")
    ]
    pairs = await asyncio.gather(*[_fetch_one(p) for p in email_prefixes])
    return dict(pairs)


def _resolve_o365_username(cfg: dict[str, Any]) -> str | None:
    """Resolve the /me account username from the MSAL token cache."""
    try:
        import json as _json
        client_id = cfg.get("client_id", "")
        if not client_id:
            return None
        config_dir = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
        cache_file = config_dir / "microsoft" / client_id / "token_cache.json"
        if not cache_file.is_file():
            return None
        data = _json.loads(cache_file.read_text(encoding="utf-8"))
        accounts = data.get("Account", {})
        if accounts:
            first = next(iter(accounts.values()))
            return first.get("username")
    except Exception:
        pass
    return None


def get_status(
    mailbox_stats_data: dict[str, dict | None] | None = None,
    fmt: str = "pipe",
) -> str:
    """Return operational status summary as a string.

    When *mailbox_stats_data* is provided (from ``get_mailbox_stats()``),
    appends a Mailbox section after Sources.
    """
    from ts4k import state

    config_dir = state.get_config_dir()
    all_cfg = _ensure_sources()
    lines: list[str] = []

    # Sources — check_token_health() may attempt network-based token refresh
    # for expired tokens. This is intentional: accurate status > fast status.
    lines.append("Sources:")
    if all_cfg:
        for prefix, cfg in sorted(all_cfg.items()):
            provider = cfg.get("provider", "?")
            detail = cfg.get("email") or cfg.get("mailbox") or cfg.get("mcp_cwd") or ""
            # For O365 /me sources missing email, resolve once and persist
            if provider == "o365" and not detail:
                username = _resolve_o365_username(cfg)
                if username:
                    detail = username
                    extra = {k: v for k, v in cfg.items() if k != "provider"}
                    sources.add(prefix, provider=provider, **extra, email=username)
            health = check_token_health(prefix, cfg)
            tag = health.status  # ok, auth, error, na
            suffix = ""
            if health.status == "auth":
                reason = f" — {health.detail}" if health.detail else ""
                suffix = f"{reason} — ts4k auth {prefix}"
            elif health.status == "error":
                suffix = f" — {health.detail}"

            lines.append(f"  {prefix:<4}{provider:<10}{detail:<30}[{tag}]{suffix}")
    else:
        lines.append("  (none — run: ts4k src add <prefix> <provider> ...)")

    # Mailbox stats (live, when provided)
    if mailbox_stats_data:
        lines.append("")
        lines.append(format_mailbox_stats(mailbox_stats_data, fmt=fmt))

    # Whatsnew keys
    from ts4k.state import keyed_watermarks
    keys = keyed_watermarks.list_keys()
    if keys:
        lines.append("")
        lines.append("Whatsnew keys:")
        for key in keys:
            wm_data = keyed_watermarks.get_all(key)
            num_sources = len(wm_data)
            last_run = max(wm_data.values()) if wm_data else "never"
            lines.append(f"  {key}: {num_sources} sources, last run {last_run}")

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
    lines.append(f"Filters:  {active_rules} active rules")

    # Stats
    st = stats.get_all()
    total_in = st.get("total_bytes_in", 0)
    total_out = st.get("total_bytes_out", 0)
    total_msgs = st.get("total_messages", 0)
    pct = stats.savings_pct()

    _provider_labels = {"gmail": "Gmail", "whatsapp": "WhatsApp", "o365": "O365", "github": "GitHub", "http": "HTTP", "o365cal": "O365 Cal", "gcal": "GCal", "caldav": "CalDAV", "carddav": "CardDAV"}

    lines.append("")
    lines.append("Stats:")
    if total_msgs > 0:
        lines.append(f"  Messages processed: {total_msgs}")
        lines.append(f"  Bytes in:  {estimate_size(total_in)} ({total_in:,})")
        lines.append(f"  Bytes out: {estimate_size(total_out)} ({total_out:,})")
        lines.append(f"  Savings:   {pct}%")

        # Show all configured sources, even those with 0 messages
        by_source = st.get("by_source", {})
        lines.append("")
        lines.append("  By source:")
        for src in sorted(all_cfg.keys()):
            provider = all_cfg[src].get("provider", "")
            if provider in _NON_MESSAGE_PROVIDERS:
                continue  # Calendars and contacts carry no messages
            base_label = _provider_labels.get(provider, provider)
            label = base_label if src == provider[0:1] else f"{base_label}({src})"
            data = by_source.get(src, {})
            src_in = data.get("bytes_in", 0)
            src_msgs = data.get("messages", 0)
            src_pct = (
                round((1 - data.get("bytes_out", 0) / src_in) * 100, 1) if src_in else 0
            )
            if src_msgs:
                lines.append(
                    f"    {label}: {src_msgs} msgs, "
                    f"{estimate_size(src_in)} in, {src_pct}% savings"
                )
            else:
                lines.append(f"    {label}: 0 msgs")

        by_cmd = st.get("by_command", {})
        if by_cmd:
            _cmd_labels = {"wn": "whatsnew", "g": "get", "l": "list", "t": "thread"}
            lines.append("")
            lines.append("  By command:")
            for cmd, data in sorted(by_cmd.items()):
                cmd_label = _cmd_labels.get(cmd, cmd)
                lines.append(
                    f"    {cmd_label}: {data.get('calls', 0)} calls, "
                    f"{estimate_size(data.get('bytes_in', 0))} in -> "
                    f"{estimate_size(data.get('bytes_out', 0))} out"
                )
    else:
        lines.append("  (no data yet — run some commands first)")

    # Cache
    cs = cache.stats()
    disk_size = estimate_size(cs['index_bytes'] + cs['bodies_bytes'])
    lines.append("")
    lines.append(f"Cache: {cs['total']} headers, {cs['bodies']} bodies, {disk_size} on disk")
    if cs["total"]:
        for src, n in sorted(cs["by_source"].items()):
            provider = all_cfg.get(src, {}).get("provider", "")
            label = _provider_labels.get(provider, provider) if provider else src
            if src != provider[0:1]:
                label = f"{label}({src})"
            lines.append(f"  {label}: {n}")

    lines.append("")
    lines.append(f"Config: {config_dir.path}  ({config_dir.reason})")

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


def _plan_contact_import(
    records: list[dict], existing: dict[str, list[str]]
) -> dict:
    """Turn CardDAV records into proposed identity-map links.

    Returns ``{"links": [(alias, [identifier, ...])], "conflicts": [str],
    "skipped": {reason: count}}``.  Nothing is written — an existing alias
    or an identifier already owned by another alias is reported as a
    conflict and left alone, so hand-made links survive an import.
    """
    from ts4k.adapters.carddav import normalize_phone

    links: list[tuple[str, list[str]]] = []
    conflicts: list[str] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    # Emit identifiers under every configured source of the matching
    # provider, so filtering by source prefix (e.g. _contact_to_query)
    # finds them regardless of what the user named their sources. Fall
    # back to the canonical letter if no such source is configured, so
    # import still works standalone.
    email_prefixes = list(sources.by_provider("gmail")) + list(sources.by_provider("o365"))
    if not email_prefixes:
        email_prefixes = ["g"]
    phone_prefixes = list(sources.by_provider("whatsapp"))
    if not phone_prefixes:
        phone_prefixes = ["w"]

    # identifier -> alias that already owns it (stored links first, then
    # links proposed earlier in this run)
    owner = {ident: alias for alias, idents in existing.items() for ident in idents}
    # aliases already proposed earlier in this run (not yet in `existing`)
    proposed: set[str] = set()

    for record in records:
        alias = " ".join(record.get("display_name", "").lower().split())
        if not alias:
            skip("no name")
            continue

        idents: list[str] = []
        seen: set[str] = set()

        def add_ident(ident: str) -> None:
            if ident not in seen:
                seen.add(ident)
                idents.append(ident)

        for email in record.get("emails", []):
            for p in email_prefixes:
                add_ident(f"{p}:{email}")
        for phone in record.get("phones", []):
            jid = normalize_phone(phone)
            if jid is None:
                skip("phone number without a country code (+ or 00)")
            else:
                for p in phone_prefixes:
                    add_ident(f"{p}:{jid}")

        if not idents:
            skip("no phone or email")
            continue

        if alias in existing:
            if set(idents) <= set(existing[alias]):
                skip("already up to date")
            else:
                conflicts.append(f"{alias}|alias already exists")
            continue

        if alias in proposed:
            conflicts.append(f"{alias}|duplicate in import")
            continue

        taken = [(i, owner[i]) for i in idents if i in owner]
        if taken:
            ident, other = taken[0]
            conflicts.append(f"{alias}|{ident} already linked to '{other}'")
            continue

        links.append((alias, idents))
        proposed.add(alias)
        for ident in idents:
            owner[ident] = alias

    return {"links": links, "conflicts": conflicts, "skipped": skipped}


def _format_contact_plan(
    prefix: str, email: str, records: list[dict], plan: dict, applied: bool
) -> str:
    """Render an import plan as the preview (or the applied summary)."""
    lines = [f"CardDAV {prefix} ({email}): {len(records)} records"]

    if plan["links"]:
        header = "LINKED" if applied else "LINKS"
        lines.append("")
        lines.append(f"{header} ({len(plan['links'])}):")
        for alias, idents in plan["links"]:
            lines.append("|".join([alias, *idents]))

    if plan["conflicts"]:
        lines.append("")
        lines.append(
            f"CONFLICTS ({len(plan['conflicts'])} — stored values left unchanged):"
        )
        lines.extend(plan["conflicts"])

    if plan["skipped"]:
        lines.append("")
        lines.append("SKIPPED:")
        for reason, count in sorted(plan["skipped"].items()):
            lines.append(f"{count}|{reason}")

    lines.append("")
    if applied:
        lines.append(f"Applied {len(plan['links'])} links to the contact map.")
    else:
        lines.append("Preview only — nothing written. Re-run with --apply to commit.")
    return "\n".join(lines)


async def sync_contacts(source: str | None = None, apply: bool = False) -> str:
    """Import an address book from a CardDAV source into the identity map.

    Preview-first: without *apply* this fetches, plans, and prints — the
    contact map is not touched.
    """
    from ts4k.adapters.carddav import CarddavAdapter, CarddavAdapterConfig
    from ts4k.auth.caldav import ICLOUD_CARDDAV_URL

    all_cfg = _ensure_sources()
    books = {
        p: c for p, c in all_cfg.items()
        if c.get("provider", "").lower() == "carddav"
    }

    if source:
        cfg = books.get(source)
        if cfg is None:
            configured = ", ".join(sorted(books)) or "none"
            return (
                f"Error: {source!r} is not a CardDAV source "
                f"(configured: {configured})."
            )
        prefix = source
    elif not books:
        return (
            "Error: no CardDAV source configured. Add one with:\n"
            "  ts4k src add ic apple-contacts email=you@icloud.com"
        )
    elif len(books) > 1:
        return (
            f"Error: multiple CardDAV sources ({', '.join(sorted(books))}) — "
            f"pick one with --source."
        )
    else:
        prefix, cfg = next(iter(books.items()))

    email = cfg.get("email", "")
    if not email:
        return f"Error: CardDAV source {prefix!r} has no email configured."

    adapter = CarddavAdapter(CarddavAdapterConfig(
        email=email,
        server_url=cfg.get("server_url", ICLOUD_CARDDAV_URL),
        config_dir=Path(cfg["config_dir"]) if cfg.get("config_dir") else None,
    ))
    async with adapter:
        records = await adapter.list_contacts()

    plan = _plan_contact_import(records, contacts.list_all())

    if apply:
        for alias, idents in plan["links"]:
            contacts.link(alias, *idents)

    return _format_contact_plan(prefix, email, records, plan, applied=apply)


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
    elif action == "add-category":
        result = filters.add_category(value or "")
        return f"skip_categories: {', '.join(result)}"
    elif action == "rm-category":
        result = filters.remove_category(value or "")
        return f"skip_categories: {', '.join(result) or '(empty)'}"
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
            f"skip_categories: {', '.join(config['skip_categories']) or '(none)'}",
        ]
        return "\n".join(lines)


async def preload(
    source: str,
    query: str | None = None,
    contact: str | None = None,
    since: str | None = None,
    pages: int = 100,
    batch_size: int = 50,
    bodies: bool = False,
    resume: str | None = None,
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
    cacheable = provider in cache.CACHEABLE_PROVIDERS

    # Gate on the provider, never the prefix: prefixes are user-chosen, so
    # a GitHub source could be named "g" or "o" and slip through a
    # prefix-based check even though nothing about it is cacheable.
    if not cacheable:
        # Fail closed: only gmail/o365 messages reach the cache, so any
        # other provider's headers would be discarded and its bodies left
        # as unreachable orphan files — reject rather than report a fake
        # success.
        return (
            f"Error: source {prefix!r} ({provider}) doesn't support preload — "
            "only Gmail and O365 messages are cached locally."
        )

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
    if resume:
        job = batch.get_job(resume)
        if job is None:
            return f"Error: job {resume!r} not found."
        if job["status"] not in ("in_progress", "failed", "stopped_disk_low"):
            return f"Error: job {resume!r} has status {job['status']!r} — cannot resume."
        job_id = resume
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
            while pages_fetched < pages:
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
                    query=effective_query, count=batch_size, page_token=page_token
                )

                if not listing:
                    break

                # Cache each entry (batch header writes — one index.json
                # write per page instead of per message)
                with cache.CacheBatch() as cb:
                    for entry in listing:
                        msg_id = entry.get("id", "")
                        if not msg_id:
                            continue
                        cb.store_header(msg_id, entry, provider=provider)

                        if bodies and cacheable:
                            try:
                                msg = await adapter.read_message(msg_id)
                                msg = _normalize_message(msg)
                                # store_header via batch; body as individual file
                                cb.store_header(msg_id, msg, provider=provider)
                                cache.store_body(msg_id, msg.get("body", ""))
                            except Exception as exc:
                                logger.warning("[%s] body fetch %s: %s", prefix, msg_id, exc)

                        if cacheable:
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


def spawn_background_preload(
    source: str,
    query: str | None = None,
    contact: str | None = None,
    since: str | None = None,
    pages: int = 100,
    batch_size: int = 50,
    bodies: bool = False,
    throttle: float = 0.2,
) -> str:
    """Spawn a detached subprocess to run preload in the background.

    Creates a batch job, launches ``python -m ts4k preload --resume <job_id>``,
    and returns immediately with the job ID.
    """
    all_cfg = _ensure_sources()
    prefixes = _resolve_prefixes(source)
    if not prefixes:
        return f"Error: no source configured for {source!r}."
    prefix = prefixes[0]
    cfg = all_cfg.get(prefix)
    if not cfg:
        return f"Error: source {prefix!r} not found."

    provider = cfg.get("provider", "").lower()

    # Resolve contact / since into effective query (same logic as preload())
    effective_query = query
    if contact and not effective_query:
        resolved = _contact_to_query(contact, prefix, provider)
        if resolved is None:
            return f"Error: no {prefix}: identifiers found for contact {contact!r}."
        effective_query = resolved
    if since and not effective_query:
        if provider == "gmail":
            effective_query = _since_to_gmail_query(since, prefix)
        elif provider == "o365":
            effective_query = since
    elif since and effective_query:
        if provider == "gmail":
            effective_query = f"{effective_query} {_since_to_gmail_query(since, prefix)}"
    if not effective_query:
        effective_query = "in:inbox" if provider == "gmail" else ""

    job_id = batch.create_job(prefix, effective_query)

    # Log directory
    from ts4k import state

    config_dir = state.get_config_dir().path
    log_dir = config_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job_id}.log"
    log_fh = open(log_path, "w", encoding="utf-8")

    # Build child argv
    argv = [sys.executable, "-m", "ts4k", "preload", "--resume", job_id, "--source", source]
    if query:
        argv += ["--query", query]
    if contact:
        argv += ["--contact", contact]
    if since:
        argv += ["--since", since]
    argv += ["--max-pages", str(pages)]
    argv += ["--page-size", str(batch_size)]
    if bodies:
        argv.append("--bodies")
    argv += ["--throttle", str(throttle)]

    # Platform-specific detach kwargs
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True

    # Propagate resolved config dir to child process
    child_env = os.environ.copy()
    child_env["TS4K_CONFIG_DIR"] = str(config_dir)

    proc = subprocess.Popen(
        argv, stdout=log_fh, stderr=subprocess.STDOUT, env=child_env, **kwargs
    )

    batch.update_job(job_id, pid=proc.pid, log_file=str(log_path))

    return (
        f"Background preload started: {job_id} (pid {proc.pid})\n"
        f"Log: {log_path}\n"
        f"Check: ts4k preload --status"
    )


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
            pid = job.get("pid")
            if pid:
                state = "running" if batch.is_running(jid) else "exited"
                line += f"|pid:{pid}({state})"
            lines.append(line)
        return "\n".join(lines)

    if action == "cancel":
        if not job_id:
            return "Error: job_id required for cancel."
        job = batch.get_job(job_id)
        if job is None:
            return f"Error: job {job_id!r} not found."
        killed = False
        if job["status"] == "in_progress":
            killed = batch.kill_job(job_id)
            batch.update_job(job_id, status="failed", error="cancelled by user")
        if killed:
            return f"Job {job_id} cancelled (process killed)."
        return f"Job {job_id} cancelled."

    return f"Unknown preload action: {action!r}"


_SOURCE_LABELS = {"g": "gmail", "o": "o365", "w": "whatsapp"}


def _parse_period(period: str) -> tuple[str, str]:
    """Parse a period string into ``(start_iso, end_iso)`` date strings.

    Supported formats:
    - ``"2025"`` → full year
    - ``"2025-Q1"`` .. ``"2025-Q4"`` → quarter
    - ``"2025-03"`` → single month
    - ``"2025-01..2025-06"`` → explicit range

    Returns ISO date strings suitable for ``<`` comparison (exclusive end).
    """
    period = period.strip()

    # Explicit range: "2025-01..2025-06"
    if ".." in period:
        parts = period.split("..", 1)
        start = parts[0].strip()
        end = parts[1].strip()
        # Compute exclusive end: next month after end
        return start, _next_month(end)

    # Quarter: "2025-Q1" .. "2025-Q4"
    upper = period.upper()
    if len(upper) == 7 and upper[4] == "-" and upper[5] == "Q" and upper[6] in "1234":
        year = upper[:4]
        q = int(upper[6])
        start_month = (q - 1) * 3 + 1
        end_month = start_month + 3
        if end_month > 12:
            return f"{year}-{start_month:02d}", f"{int(year) + 1}-01"
        return f"{year}-{start_month:02d}", f"{year}-{end_month:02d}"

    # Year: "2025"
    if len(period) == 4 and period.isdigit():
        return f"{period}-01", f"{int(period) + 1}-01"

    # Month: "2025-03"
    if len(period) == 7 and period[4] == "-" and period[5:].isdigit():
        return period, _next_month(period)

    # Fallback: treat as start, no end
    return period, "9999-99"


def _next_month(ym: str) -> str:
    """Given ``'YYYY-MM'``, return the next month as ``'YYYY-MM'``."""
    try:
        year = int(ym[:4])
        month = int(ym[5:7])
    except (ValueError, IndexError):
        return "9999-99"
    month += 1
    if month > 12:
        month = 1
        year += 1
    return f"{year}-{month:02d}"


def _sibling_prefixes(sources_cfg: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Map each configured source prefix to every prefix sharing its provider.

    Built once per overview/list call (not per message) so a contact
    imported under one configured source of a provider (e.g. a custom
    prefix like "gw" for Gmail) still resolves for messages arriving
    under a sibling source of the same provider type.
    """
    by_provider: dict[str, list[str]] = {}
    for prefix, cfg in sources_cfg.items():
        by_provider.setdefault(cfg.get("provider", "").lower(), []).append(prefix)
    return {
        prefix: by_provider.get(cfg.get("provider", "").lower(), [prefix])
        for prefix, cfg in sources_cfg.items()
    }


def _resolve_sender(
    from_field: str,
    source: str = "",
    sibling_prefixes: dict[str, list[str]] | None = None,
) -> str:
    """Collapse a from-field to a contact alias if linked, otherwise raw.

    *source* is the message's own configured source prefix (e.g. "gw" for
    a custom-prefixed Gmail source) — tried first, since imported
    identifiers are stored under whatever prefix the source actually uses,
    not necessarily the canonical letter. *sibling_prefixes* (see
    ``_sibling_prefixes``) extends the search to every other configured
    source of the same provider type before falling back to the static
    canonical letters, which still cover an unconfigured/standalone lookup.
    """
    # Direct match (e.g. from_field is already "g:alice@x.com")
    alias = contacts.resolve(from_field)
    if alias:
        return alias
    # Try stripping source prefix (e.g. "g:alice@x.com" → "alice@x.com")
    if ":" in from_field:
        raw = from_field.split(":", 1)[1]
        alias = contacts.resolve(raw)
        if alias:
            return alias
        return from_field

    tried: set[str] = set()

    def try_prefix(prefix: str) -> str | None:
        if prefix in tried:
            return None
        tried.add(prefix)
        return contacts.resolve(f"{prefix}:{from_field}")

    if source:
        alias = try_prefix(source)
        if alias:
            return alias
        for prefix in (sibling_prefixes or {}).get(source, []):
            alias = try_prefix(prefix)
            if alias:
                return alias

    # Fall back to the canonical letters, for callers that don't have
    # (or don't need) a sources config — e.g. direct tests.
    for prefix in _SOURCE_LABELS:
        alias = try_prefix(prefix)
        if alias:
            return alias
    return from_field


def _filter_by_contact(
    headers: list[dict], contact: str
) -> list[dict]:
    """Expand contact → identifiers, filter headers by from-field match."""
    idents = contacts.query(contact.lower().strip())
    if not idents:
        matches = contacts.find(contact.lower().strip())
        if matches:
            idents = next(iter(matches.values()))

    if not idents:
        # Fallback: substring match on from field
        term = contact.lower()
        return [h for h in headers if term in h.get("from", "").lower()]

    # Build a set of raw identifiers (strip source prefix)
    raw_set: set[str] = set()
    for ident in idents:
        raw_set.add(ident.lower())
        if ":" in ident:
            raw_set.add(ident.split(":", 1)[1].lower())

    return [
        h for h in headers
        if h.get("from", "").lower() in raw_set
        or any(r in h.get("from", "").lower() for r in raw_set)
    ]


def _live_meters(all_cfg: dict[str, dict[str, Any]]) -> dict[str, list[dict]]:
    """Meter snapshots (#31) for prefixes still configured as HTTP sources.

    ``src rm`` doesn't touch meters.json, so filtering here — rather than
    deleting the snapshot on removal — keeps a removed (or repurposed)
    source's stale snapshot from lingering in `overview()` forever, and
    also heals any snapshot files that already went stale before this fix.
    """
    return {
        src: m for src, m in meters.all_meters().items()
        if m and all_cfg.get(src, {}).get("provider", "").lower() == "http"
    }


def _build_top_view(headers: list[dict], top: int) -> dict:
    """Build the top-level overview: group by source, count senders."""
    by_source: dict[str, list[dict]] = {}
    for h in headers:
        src = h.get("source", "?")
        by_source.setdefault(src, []).append(h)

    all_cfg = _ensure_sources()
    sibling_prefixes = _sibling_prefixes(all_cfg)
    live_meters = _live_meters(all_cfg)

    sources_list = []
    for src in sorted(by_source.keys()):
        msgs = by_source[src]
        dates = [m.get("date", "") for m in msgs if m.get("date")]
        sender_counts: dict[str, int] = {}
        for m in msgs:
            sender = _resolve_sender(m.get("from", ""), m.get("source", ""), sibling_prefixes)
            sender_counts[sender] = sender_counts.get(sender, 0) + 1
        top_senders = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)[:top]
        entry = {
            "prefix": src,
            "label": _SOURCE_LABELS.get(src, src),
            "count": len(msgs),
            "date_start": min(dates)[:7] if dates else "",
            "date_end": max(dates)[:7] if dates else "",
            "top_senders": [{"name": n, "count": c} for n, c in top_senders[:5]],
        }
        # Same staleness filter as the meter-only loop below — a prefix that
        # once served an HTTP source but now has cached messages under a
        # different provider must not keep showing the old snapshot.
        src_meters = live_meters.get(src)
        if src_meters:
            entry["meters"] = src_meters
        sources_list.append(entry)

    # Sources with a live meter snapshot (#31) but no cached messages — HTTP
    # notification sources poll live and aren't written to the message
    # cache (like WhatsApp), so they'd otherwise be invisible here.
    for src, src_meters in sorted(live_meters.items()):
        if src in by_source:
            continue
        sources_list.append({
            "prefix": src,
            "label": _SOURCE_LABELS.get(src, src),
            "count": 0,
            "date_start": "",
            "date_end": "",
            "top_senders": [],
            "meters": src_meters,
        })

    return {
        "level": "top",
        "total": len(headers),
        "source_count": len(by_source),
        "sources": sources_list,
    }


def _build_source_view(
    headers: list[dict], source: str, top: int
) -> dict:
    """Top senders + top threads for a single source."""
    filtered = [h for h in headers if h.get("source") == source]
    dates = [m.get("date", "") for m in filtered if m.get("date")]

    sibling_prefixes = _sibling_prefixes(_ensure_sources())

    # Top senders
    sender_counts: dict[str, int] = {}
    for m in filtered:
        sender = _resolve_sender(m.get("from", ""), source, sibling_prefixes)
        sender_counts[sender] = sender_counts.get(sender, 0) + 1
    top_senders = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)[:top]

    # Top threads (only when enough entries have thread_id)
    thread_counts: dict[str, dict] = {}
    for m in filtered:
        tid = m.get("thread_id")
        if tid:
            if tid not in thread_counts:
                thread_counts[tid] = {
                    "id": tid,
                    "subject": m.get("subject", ""),
                    "count": 0,
                }
            thread_counts[tid]["count"] += 1

    top_threads = []
    if len(thread_counts) >= 3:
        top_threads = sorted(
            thread_counts.values(), key=lambda x: x["count"], reverse=True
        )[:top]

    return {
        "level": "source",
        "prefix": source,
        "label": _SOURCE_LABELS.get(source, source),
        "total": len(filtered),
        "date_start": min(dates)[:7] if dates else "",
        "date_end": max(dates)[:7] if dates else "",
        "top_senders": [{"name": n, "count": c} for n, c in top_senders],
        "top_threads": top_threads,
    }


def _build_period_breakdown(headers: list[dict]) -> list[dict]:
    """Group headers into quarterly buckets."""
    buckets: dict[str, int] = {}
    for h in headers:
        d = h.get("date", "")
        if len(d) >= 7:
            year = d[:4]
            try:
                month = int(d[5:7])
            except ValueError:
                continue
            quarter = (month - 1) // 3 + 1
            key = f"{year}-Q{quarter}"
            buckets[key] = buckets.get(key, 0) + 1

    return [{"period": k, "count": v} for k, v in sorted(buckets.items())]


def _build_contact_view(
    headers: list[dict], contact: str, top: int
) -> dict:
    """By-source + by-period breakdown for a contact."""
    by_source: dict[str, list[dict]] = {}
    for h in headers:
        src = h.get("source", "?")
        by_source.setdefault(src, []).append(h)

    sources_list = []
    for src in sorted(by_source.keys()):
        msgs = by_source[src]
        dates = [m.get("date", "") for m in msgs if m.get("date")]
        sources_list.append({
            "prefix": src,
            "label": _SOURCE_LABELS.get(src, src),
            "count": len(msgs),
            "date_start": min(dates)[:7] if dates else "",
            "date_end": max(dates)[:7] if dates else "",
        })

    periods = _build_period_breakdown(headers)

    return {
        "level": "contact",
        "contact": contact,
        "total": len(headers),
        "source_count": len(by_source),
        "sources": sources_list,
        "periods": periods,
    }


def overview(
    source: str | None = None,
    contact: str | None = None,
    period: str | None = None,
    fmt: str = "pipe",
    top: int = 10,
) -> str:
    """Hierarchical overview of cached messages. Cache-only, no adapter calls.

    Three view levels based on arguments:
    - Top-level (no flags): group by source → count, date range, top senders
    - Source drill-down (--source g): top senders, top threads
    - Contact drill-down (--contact alice): messages across sources, quarterly breakdown
    """
    # Load all cached headers
    headers = cache.list_headers(source=source if source and not contact else None)

    # Period filter
    if period:
        start, end = _parse_period(period)
        headers = [
            h for h in headers
            if h.get("date", "") >= start and h.get("date", "") < end
        ]

    # Contact filter
    if contact:
        headers = _filter_by_contact(headers, contact)
        if source:
            headers = [h for h in headers if h.get("source") == source]

    if not headers:
        if contact:
            return f"No cached messages for contact {contact!r}."
        if source:
            return f"No cached messages for source {source!r}."
        # An HTTP-only setup (#31) has no cached messages — it polls live —
        # but still has meter snapshots to show; only bail out to the
        # cache-empty message when there's truly nothing to display.
        if not _live_meters(_ensure_sources()):
            return "Cache is empty. Run: ts4k preload --source <prefix>"

    # Build the appropriate view
    if contact:
        data = _build_contact_view(headers, contact, top)
    elif source:
        data = _build_source_view(headers, source, top)
    else:
        data = _build_top_view(headers, top)

    return format_overview(data, fmt=fmt)


def manage_cache(
    action: str | None = None,
    source: str | None = None,
    stale: bool = False,
) -> str:
    """Manage message cache. Returns output string."""
    if action == "clear":
        removed = cache.clear(source=source, stale_only=stale)
        scope = f"source {source}" if source else "all sources"
        qualifier = " stale" if stale else ""
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


# ---------------------------------------------------------------------------
# Manage commands (#19)
# ---------------------------------------------------------------------------


async def _thread_id_for(adapter: Any, mid: str) -> str:
    """Map a message ID to its thread ID.

    Cache first; one read as fallback.  A ref from a ``--threads`` listing is
    already a thread ID — the read fails there, so *mid* passes through.
    """
    cached = cache.get_message(mid)
    tid = (cached or {}).get("thread_id")
    if tid:
        return tid
    try:
        msg = await adapter.read_message(mid)
    except Exception:
        return mid
    return msg.get("thread_id") or mid


async def manage_message(
    action: str,
    msg_id: str,
    label: str | None = None,
    folder: str | None = None,
    dry_run: bool = False,
    ref_table: RefTable | None = None,
    thread: bool = False,
) -> str:
    """Execute a management action on one or more messages.

    msg_id can be comma-separated for batch operations.
    Actions: archive, unarchive, label, unlabel, read, unread, trash, list-labels.

    When *thread* is set the action covers every message in each thread
    (Gmail only) — one API call per thread.
    """
    # Resolve refs to full IDs
    ids = [_resolve_ref(mid.strip(), ref_table) for mid in msg_id.split(",")]

    if action == "list-labels":
        prefix = ids[0].split(":")[0] if ids else None
        if not prefix:
            return "Error: provide a source-prefixed ID (e.g. g:123)"
        cfg = sources.get(prefix)
        if not cfg:
            return f"Error: unknown source {prefix!r}"
        adapter = _make_adapter(prefix, cfg)
        if not adapter:
            return f"Error: failed to create adapter for {prefix!r}"
        try:
            async with adapter:
                labels = await adapter.list_labels()
            lines = [f"{lbl['name']} ({lbl.get('type', '')})" for lbl in labels]
            return "\n".join(lines) if lines else "No labels found."
        except PermissionError as e:
            return f"Error: {e}"
        except NotImplementedError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing labels: {e}"

    results = []
    thread_calls: dict[tuple[str, str], tuple[str, dict]] = {}
    for mid in ids:
        prefix = mid.split(":")[0] if ":" in mid else None
        if not prefix:
            results.append(f"{mid}: error — missing source prefix")
            continue

        if dry_run:
            detail = ""
            if label:
                detail += f" label={label}"
            if folder:
                detail += f" folder={folder}"
            scope = " (whole thread)" if thread else ""
            results.append(f"{mid}: would {action}{scope}{detail}")
            continue

        cfg = sources.get(prefix)
        if not cfg:
            results.append(f"{mid}: error — unknown source {prefix!r}")
            continue

        adapter = _make_adapter(prefix, cfg)
        if not adapter:
            results.append(f"{mid}: error — failed to create adapter")
            continue

        try:
            async with adapter:
                if thread:
                    if action in ("label", "unlabel") and not label:
                        results.append(f"{mid}: error — --label required")
                        continue
                    tid = await _thread_id_for(adapter, mid)
                    key = (prefix, tid)
                    if key in thread_calls:
                        first_mid, prev_r = thread_calls[key]
                        results.append(
                            f"{mid}: {prev_r.get('status', 'ok')} "
                            f"(same thread as {first_mid}, already {action}ed)"
                        )
                        continue
                    r = await adapter.modify_thread(tid, action, label)
                    thread_calls[key] = (mid, r)
                elif action == "archive":
                    r = await adapter.archive_message(mid)
                elif action == "unarchive":
                    r = await adapter.unarchive_message(mid)
                elif action == "label":
                    if not label:
                        results.append(f"{mid}: error — --label required")
                        continue
                    r = await adapter.label_message(mid, label)
                elif action == "unlabel":
                    if not label:
                        results.append(f"{mid}: error — --label required")
                        continue
                    r = await adapter.unlabel_message(mid, label)
                elif action == "read":
                    r = await adapter.mark_read(mid)
                elif action == "unread":
                    r = await adapter.mark_unread(mid)
                elif action == "trash":
                    r = await adapter.trash_message(mid)
                elif action == "move":
                    if not folder:
                        results.append(f"{mid}: error — --folder required")
                        continue
                    r = await adapter.move_to_folder(mid, folder)
                else:
                    results.append(f"{mid}: error — unknown action {action!r}")
                    continue
                results.append(f"{mid}: {r.get('status', 'ok')}")
        except PermissionError as e:
            results.append(f"{mid}: {e}")
        except NotImplementedError as e:
            if thread:
                results.append(
                    f"{mid}: error — thread operations not supported for "
                    f"source {prefix!r} (Gmail only)"
                )
            else:
                results.append(f"{mid}: error — {e}")
        except Exception as e:
            results.append(f"{mid}: error — {e}")

    return "\n".join(results)



# ---------------------------------------------------------------------------
# Draft commands (#18)
# ---------------------------------------------------------------------------


async def create_draft(
    source: str,
    to: str,
    subject: str,
    body: str,
    reply_to: str | None = None,
    ref_table: RefTable | None = None,
) -> str:
    """Create a draft message on the specified source."""
    cfg = sources.get(source)
    if not cfg:
        # Try provider name
        by_prov = sources.by_provider(source)
        if by_prov:
            source = next(iter(by_prov))
            cfg = by_prov[source]
        else:
            return f"Error: unknown source {source!r}"

    adapter = _make_adapter(source, cfg)
    if not adapter:
        return f"Error: failed to create adapter for {source!r}"

    reply_id = None
    if reply_to:
        reply_id = _resolve_ref(reply_to, ref_table)

    try:
        async with adapter:
            result = await adapter.create_draft(
                to=to, subject=subject, body=body,
                reply_to_message_id=reply_id,
            )
        return f"Draft created: {result['id']} ({result['status']})"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error creating draft: {e}"


def llm_help() -> str:
    """Return context-aware, agent-optimized help reference.

    Adapts output based on current state:
    - No sources → lead with setup
    - Sources but missing auth → lead with auth
    - Healthy → lead with commands
    """
    from ts4k import state

    all_cfg = sources.list_all()
    lines: list[str] = []

    lines.append("ts4k — Token Saver 4000 (agent reference)")
    lines.append("")

    # --- Context-aware lead section ---
    if not all_cfg:
        lines.append("STATUS: No sources configured. Complete setup first.")
        lines.append("")
        _append_setup(lines)
        lines.append("")
        _append_commands(lines)
    else:
        # Check for sources that might need auth
        needs_auth = _sources_needing_auth(all_cfg)
        lines.append("SOURCES:")
        for prefix, cfg in sorted(all_cfg.items()):
            provider = cfg.get("provider", "?")
            detail = cfg.get("email") or cfg.get("mailbox") or cfg.get("mcp_cwd") or ""
            level = cfg.get("level", "readonly")
            auth_status = " [needs auth]" if prefix in needs_auth else ""
            lines.append(f"  {prefix}: {provider} ({detail}) [{level}]{auth_status}")
        lines.append("")

        if needs_auth:
            lines.append("ACTION REQUIRED: Re-authenticate stale sources:")
            for prefix in needs_auth:
                lines.append(f"  ts4k auth {prefix}")
            lines.append("")

        _append_commands(lines)
        lines.append("")
        _append_setup(lines)

    lines.append("")
    _append_mistakes(lines)
    lines.append("")
    _append_errors(lines)
    lines.append("")
    config_dir = state.get_config_dir()
    lines.append(f"CONFIG: {config_dir.path}")
    lines.append("DOCS: https://github.com/peterdrier/ts4k/tree/main/docs")

    return "\n".join(lines)


def check_token_health(prefix: str, cfg: dict[str, Any]) -> "TokenHealth":
    """Check token health for a source, dispatching by provider.

    Returns a TokenHealth with status ok/auth/error/na.
    WhatsApp returns ok (session-based, no token to validate).
    Unknown providers return na.

    Uses lazy imports so patches on validate_token() work correctly in tests.
    """
    from ts4k.auth.health import TokenHealth

    provider = cfg.get("provider", "").lower()

    if provider == "whatsapp":
        # The WhatsApp session itself lives in the bridge and has no token ts4k
        # can validate. The HTTP transport does have one — the bridge's HMAC
        # key — and a missing key is the failure an operator hits first.
        if (cfg.get("transport") or "http").lower() == "stdio":
            return TokenHealth(status="ok", expiry=None, scopes=[], detail="session-based")
        if wa_bridge_auth.resolve_bridge_token(
            cfg.get("bridge_token"), cfg.get("bridge_token_file")
        ):
            return TokenHealth(status="ok", expiry=None, scopes=[], detail="bridge key configured")
        return TokenHealth(
            status="auth", expiry=None, scopes=[],
            detail="no bridge key: set bridge_token_file=<bridge>/store/api_token",
        )

    if provider in ("gmail", "gcal"):
        from ts4k.auth.google import validate_token
        email = cfg.get("email", "")
        if not email:
            return TokenHealth(status="na", expiry=None, scopes=[], detail="no email configured")
        # Pass required scopes so we can detect under-scoped tokens
        from ts4k.core.levels import scopes_for, parse_level
        required = scopes_for(provider, parse_level(cfg.get("level")))
        return validate_token(email, required_scopes=required or None)

    if provider in ("o365", "o365cal"):
        from ts4k.auth.microsoft import validate_token
        client_id = cfg.get("client_id", "")
        tenant_id = cfg.get("tenant_id", "common") or "common"
        username = cfg.get("mailbox") or cfg.get("email")
        # Pass source-specific scopes instead of defaulting to mail-read
        from ts4k.core.levels import scopes_for, parse_level
        required = scopes_for(provider, parse_level(cfg.get("level")))
        return validate_token(client_id, tenant_id=tenant_id, scopes=required or None, username=username)

    if provider == "github":
        # A PAT can only be validated by spending an API call, and this runs
        # on every `ts4k status` — report whether one resolves, not whether
        # GitHub still accepts it.  A revoked token surfaces as a 401 on use.
        from ts4k.adapters.github import resolve_token
        if resolve_token(cfg.get("token"), cfg.get("token_file")):
            return TokenHealth(status="ok", expiry=None, scopes=[], detail="PAT configured")
        return TokenHealth(
            status="auth", expiry=None, scopes=[],
            detail=f"no token — run: ts4k src add {prefix} github token_file=<path>",
        )

    if provider in ("caldav", "carddav"):
        # Both share one app-specific password per Apple ID — except a
        # generic (non-iCloud) CardDAV source, which is keyed separately.
        from ts4k.auth.caldav import ICLOUD_CARDDAV_URL, load_credentials
        email = cfg.get("email", "")
        if not email:
            return TokenHealth(status="na", expiry=None, scopes=[], detail="no email configured")
        if provider == "carddav":
            from ts4k.adapters.carddav import carddav_credential_key
            key = carddav_credential_key(email, cfg.get("server_url", ICLOUD_CARDDAV_URL))
        else:
            key = email
        if load_credentials(key, Path(cfg["config_dir"]) if cfg.get("config_dir") else None) is None:
            from ts4k.auth.caldav import is_icloud_carddav_url
            server_url = cfg.get("server_url", ICLOUD_CARDDAV_URL)
            if provider == "carddav" and not is_icloud_carddav_url(server_url):
                # Suggesting the apple-contacts preset here would store an
                # iCloud credential under a different key and clobber the
                # generic source's endpoint
                repair = f"ts4k src add <prefix> carddav email={email} server_url={server_url}"
            else:
                alias = "apple-contacts" if provider == "carddav" else "apple"
                repair = f"ts4k src add <prefix> {alias} email={email}"
            return TokenHealth(status="na", expiry=None, scopes=[],
                               detail=f"no credentials — run: {repair}")
        return TokenHealth(status="ok", expiry=None, scopes=[], detail="app-specific password")

    return TokenHealth(status="na", expiry=None, scopes=[], detail=f"unknown provider: {provider}")


def _sources_needing_auth(all_cfg: dict[str, dict[str, Any]]) -> list[str]:
    """Return prefixes of sources that likely need (re-)authentication."""
    needs: list[str] = []
    for prefix, cfg in all_cfg.items():
        health = check_token_health(prefix, cfg)
        if health.status in ("auth", "error"):
            needs.append(prefix)
    return needs


def _append_commands(lines: list[str]) -> None:
    """Append command reference to lines."""
    lines.append("COMMANDS:")
    lines.append("  ts4k list [--since T] [-q Q] [--source S] [-n N] [-k K] [--from ADDR] [--domain D] [--threads]  Search messages (all filters stack)")
    lines.append("  ts4k whatsnew KEY [--source S] [-n N]            Check new (keyed watermarks)")
    lines.append("  ts4k get [-k KEY] ID                            Read single message body")
    lines.append("  ts4k thread [-k KEY] TID                        Read thread/conversation")
    lines.append("  ts4k overview [--source S] [--contact C]        Cache summary drill-down")
    lines.append("  ts4k manage ACTION ID [-k KEY] [--label L] [--folder F] [--thread] [--dry-run]  Manage mailbox (archive, label, read/unread, trash)")
    lines.append("  ts4k draft create -s S --to ADDR --subject SUBJ --body TEXT  Create draft (never sends)")
    lines.append("  ts4k status                                     Health, stats, efficiency")
    lines.append("  Access levels: readonly (default), modify (manage), draft (create drafts). Set via: ts4k src add PREFIX PROVIDER level=modify")
    lines.append("  Refs: listings assign numbers (1, 2, ...) — use with get/thread/manage. Refs accumulate across calls.")
    lines.append("  --threads: one row per thread (refs resolve to threads). --thread on manage acts on the whole thread (Gmail).")
    lines.append("  list refs are global: get N. list/whatsnew -k refs are keyed: get -k KEY N")
    lines.append("  IDs use prefix:id format: g:abc123, o:AAM..., w:3EB...")
    lines.append("  --since: 2d, 6h, 1w, ISO date, or 'all'. --from: sender address. --domain: sender domain.")
    lines.append("  Truncated results show a continuation command — copy-paste for older messages.")
    lines.append("  Output formats: -f p (pipe, default) | -f j (JSON) | -f x (XML)")
    lines.append("  Filters (off by default): add -F to apply skip filters")
    lines.append("  get --readable: human-readable body (paragraphs, bold/italic, markdown tables) — for display, not agent use")
    lines.append("")
    lines.append("CALENDAR:")
    lines.append("  ts4k cal [today]                                  Today's events (default)")
    lines.append("  ts4k cal tomorrow                                 Tomorrow's events")
    lines.append("  ts4k cal week                                     This week (Mon-Sun)")
    lines.append("  ts4k cal next [-n N]                              Next N events (default 10)")
    lines.append("  ts4k cal range --from DATE --to DATE              Events in date range")
    lines.append("  ts4k cal event REF                                Full event detail")
    lines.append("  ts4k cal setup                                    Discover and add calendar sources")
    lines.append("  ts4k cal create -s S --title T --start DT --end DT  Create event")
    lines.append("  ts4k cal update REF [--title T] [--start DT]     Update event fields")
    lines.append("  ts4k cal rsvp REF --status accepted|declined|tentative  RSVP to event")


def _append_setup(lines: list[str]) -> None:
    """Append setup instructions to lines."""
    lines.append("SETUP (per provider):")
    lines.append("  Gmail:")
    lines.append("    1. Get client_secret.json: Google Cloud Console > APIs & Services > Credentials > OAuth 2.0 > Download JSON")
    lines.append("    2. mkdir -p ~/.config/ts4k/google/<email>/ && cp client_secret.json there")
    lines.append("    3. ts4k src add g gmail email=<email>")
    lines.append("    4. ts4k auth g                              (opens browser for OAuth)")
    lines.append("    5. ts4k list --source g --since 2d           (verify)")
    lines.append("  O365:")
    lines.append("    1. Register app: Azure Portal > App registrations > New > add Mail.Read permission")
    lines.append("       Permission GUIDs: https://learn.microsoft.com/en-us/graph/permissions-reference")
    lines.append("    2. ts4k src add o o365 client_id=<id> tenant_id=<tid>")
    lines.append("    3. ts4k auth o                              (device code flow)")
    lines.append("    4. ts4k src discover                        (find mailboxes)")
    lines.append("    5. ts4k list --source o --since 2d          (verify)")
    lines.append("  GitHub (notifications, issues, PRs — read + mark-read only):")
    lines.append("    1. Create a fine-grained PAT: github.com/settings/tokens — Notifications: read, Issues/Pull requests: read")
    lines.append("       (use Notifications: write instead of read if you'll set level=modify, below, to mark notifications read)")
    lines.append("    2. ts4k src add gh github token_file=<path>   (or GITHUB_TOKEN env; level=modify to allow mark-read)")
    lines.append("    3. ts4k whatsnew dev --source gh             (verify)")
    lines.append("    Categories: ci, review, mention, assignment, dependabot, release — suppress with: ts4k filter add-category ci")
    lines.append("  Calendar (Google & O365):")
    lines.append("    1. Google: same OAuth credentials as Gmail (shared token)")
    lines.append("    2. O365: same auth as O365 mail (calendar scopes added automatically)")
    lines.append("    3. ts4k cal setup                           (discover calendars, auto-adds sources)")
    lines.append("    4. ts4k cal                                 (verify — shows today's events)")
    lines.append("  WhatsApp (talks HTTP MCP to the Go bridge; no subprocess):")
    lines.append("    1. ts4k src add w whatsapp bridge_token_file=/path/to/whatsapp-bridge/store/api_token")
    lines.append("       (bridge_url defaults to http://127.0.0.1:18741/mcp)")
    lines.append("    2. ts4k list --source w --since 2d          (verify)")
    lines.append("    Rollback to the Python stdio server:")
    lines.append('       ts4k src add w whatsapp transport=stdio mcp_cwd=/path/to/whatsapp-mcp-server server_command="uv run python main.py"')
    lines.append("  HTTP notifications (any JSON endpoint returning {notifications, meters}):")
    lines.append('    1. ts4k src add h http url=<url> header="X-Api-Key: <key>"')
    lines.append("       (repeat header= or comma-separate for multiple auth headers)")
    lines.append("    2. ts4k list --source h                     (verify)")


def _append_errors(lines: list[str]) -> None:
    """Append error→fix mappings to lines."""
    lines.append("ERRORS:")
    lines.append('  "No client_secret.json" -> get OAuth JSON from Google Cloud Console, place in ~/.config/ts4k/google/<email>/')
    lines.append('  "No sources configured" -> ts4k src add <prefix> <provider> ...')
    lines.append('  "adapter not connected" -> ts4k status, then re-run ts4k auth')
    lines.append('  "auth expired" -> ts4k auth <prefix> (in a terminal with browser)')
    lines.append('  "Invalid message ID" -> use prefix:id format, e.g. g:abc123')
    lines.append('  WhatsApp validation error -> check server_command is a list in sources.json')


def _append_mistakes(lines: list[str]) -> None:
    """Append common agent mistakes to lines."""
    lines.append("COMMON MISTAKES:")
    lines.append("  WRONG: ts4k g inbox              -> RIGHT: ts4k list --source g --since 1d")
    lines.append("  WRONG: ts4k list --hours 24      -> RIGHT: ts4k list --since 24h")
    lines.append("  WRONG: ts4k gmail whatsnew        -> RIGHT: ts4k whatsnew life --source gmail")
    lines.append("  WRONG: ts4k list g                -> RIGHT: ts4k list --source g")
    lines.append("  WRONG: ts4k get abc123            -> RIGHT: ts4k get g:abc123")
    lines.append("  WRONG: ts4k get #7                -> RIGHT: ts4k get 7 (or get -k life 7)")
    lines.append("  WRONG: ts4k get -k life 7 (after list) -> RIGHT: ts4k get 7 (or use list -k life)")


def skill_reference(level: str = "basic") -> str:
    """Return compact command reference for skill mode.

    Args:
        level: "basic" for everyday commands (tier 1),
               "more" for admin/power commands (tier 2).
    """
    if level == "more":
        return (
            "ts4k admin commands:\n"
            "preload --source S [--query Q] [--bg]|Paginate history into cache\n"
            "preload --status|Show preload jobs\n"
            "contacts link ALIAS ID [ID...]|Link identifiers to alias\n"
            "contacts unlink|find|list|Manage contacts\n"
            "contacts sync [--apply]|Import iCloud address book (preview by default)\n"
            "filter show|add-sender|rm-sender|add-domain|rm-domain|add-pattern|rm-pattern|add-category|rm-category|skip-groups|reset\n"
            "  GitHub categories: ci|review|mention|assignment|dependabot|release\n"
            "cache stats|clear [--source S] [--stale]|Manage cache\n"
            "manage actions: archive, unarchive, label, unlabel, read, unread, trash, move, list-labels\n"
            "  manage archive 1,2,3|Batch archive by ref\n"
            "  manage label 5 --label llm-garbage|Add label by ref\n"
            "  manage archive 1 --thread|Archive every message in the thread\n"
            "  manage read 1,2 -k work --dry-run|Preview with keyed refs\n"
            "draft create -s g --to alice@x.com --subject Hi --body Hello|New draft\n"
            "  draft create -s g --reply-to g:abc --body 'Sounds good!'|Reply draft with threading\n"
            "cal create -s gc --title T --start DT --end DT [--attendees a@x,b@x]|Create event\n"
            "cal update REF [--title T] [--start DT] [--end DT]|Update event fields\n"
            "cal rsvp REF --status accepted|declined|tentative|RSVP to event\n"
            "cal setup|Discover and add calendar sources\n"
            "thread REF --format convo|Compact one-line-per-message transcript\n"
            "help|Human-readable help"
        )
    return (
        "ts4k \u2014 token-efficient messaging gateway\n"
        "list [--since T] [-q Q] [--source S] [-n N] [-k KEY] [--from ADDR] [--domain D] [--threads]|Search messages (all filters stack; --threads collapses to one row per thread)\n"
        "whatsnew KEY [-n N] [--source S]|New msgs since last check (all sources unless --source)\n"
        "get REF [-k KEY]|Read message. get 3 (list ref) | get 7 -k life (whatsnew/list -k ref) | get g:abc123 (native ID, no -k). No --source on get.\n"
        "thread [-k KEY] REF|Read thread (e.g. thread 5)\n"
        "overview [--source S] [--contact C] [--period P]|Cache summary\n"
        "manage ACTION ID [-k KEY] [--label L] [--folder F] [--thread] [--dry-run]|Manage mailbox (requires level=modify; --thread acts on the whole Gmail thread)\n"
        "draft create -s S --to ADDR --subject SUBJ --body TEXT [--reply-to ID]|Create draft (requires level=draft)\n"
        "cal [today|tomorrow|week] [-s SOURCE]|Calendar events (default: today)\n"
        "cal next [N] [-s SOURCE]|Next N events (default 10)\n"
        "cal range --from DATE --to DATE [-s SOURCE]|Events in date range\n"
        "cal event REF|Full event detail (attendees, description, meeting link)\n"
        "cal create -s S --title T --start DT --end DT [--attendees a@b,c@d] [--location L]|Create event (requires level=draft/send)\n"
        "cal update REF [--title T] [--start DT] [--end DT]|Update event (requires level=modify)\n"
        "cal rsvp REF --status accepted|declined|tentative|RSVP to event (requires level=modify)\n"
        "cal setup|Discover & add calendar sources from Gmail and O365 accounts\n"
        "status|Health + stats\n"
        "Access levels: readonly (default), modify, draft, send. Set: ts4k src add PREFIX PROVIDER level=modify\n"
        "Keys: user-defined labels (life, work). Each key tracks its own read position.\n"
        "  whatsnew life \u2192 shows unseen msgs, advances watermark. Next call shows only newer.\n"
        "  whatsnew life -n 50 \u2192 all sources, one call. --source S narrows to one source.\n"
        "Refs: listings assign numbers (1, 2, ...). Use with get/thread/manage/event. Refs accumulate across calls.\n"
        "  list refs are global: get N. whatsnew -k refs are keyed: get N -k KEY.\n"
        "Source prefixes are user-chosen (ts4k sources to list). --since: 2d, 6h, 1w, ISO. -f p|j|x. -F filters.\n"
        "Check ts4k sources for per-source activity/notes before widening --since.\n"
        "Source is a FLAG (--source g), not a subcommand.\n"
        "Sender/domain/time: --from alice@co.com, --domain co.com, --since 1w. All stack.\n"
        "  Find all from a domain: list --domain co.com --since 2023-01-01 -n 200\n"
        "  Truncated results show a continuation command \u2014 copy-paste to get older messages.\n"
        "WhatsApp voice notes are auto-transcribed: body IS the transcript, prefixed [voice 2:11]. "
        "Never ask the user to listen. [voice — transcript unavailable] means no text exists.\n"
        "WhatsApp images are auto-captioned/OCR'd: body carries [image: caption | text: OCR]. "
        "Never say you can't see images. [image — enrichment pending] means processing, not "
        "missing. get REF --media downloads the file and prints its local path.\n"
        "Do NOT pipe through head/grep/awk. Use built-in flags instead:\n"
        "  Limit results: -n 20 (not | head). Search: list -q \"name\" (not | grep).\n"
        "  One call: whatsnew KEY -n 50 (not per-source calls).\n"
        "More: ts4k skill more | Setup: ts4k skill setup"
    )


def skill_template() -> str:
    """Return the SKILL.md content for Claude Code skill installation."""
    return (
        '---\n'
        'name: ts\n'
        'description: "Token-efficient messaging and calendar gateway for Gmail, WhatsApp,'
        ' O365, GitHub, and Google Calendar. Use when the user asks about email, messages, inbox,'
        ' communications, mail, calendar, events, meetings, schedule, or wants to check,'
        ' read, search, or manage their messages or calendar across platforms. Always use'
        ' this skill for any email, messaging, or calendar task — even if the user doesn\'t'
        ' mention ts4k by name."\n'
        'allowed-tools: Bash(ts4k *)\n'
        '---\n'
        '\n'
        'Run `ts4k skill` for commands.\n'
    )


# ---------------------------------------------------------------------------
# Calendar commands
# ---------------------------------------------------------------------------


async def _cal_fetch_events(
    source: str | None,
    time_min: str,
    time_max: str,
    count: int = 250,
    tz: "tzinfo | None" = None,
) -> list[dict]:
    """Fetch events from all calendar sources (or a specific one), merge by start time."""
    from ts4k.state import sources as src_mod

    all_sources = src_mod.list_all()
    # Route through _resolve_prefixes so provider aliases (apple, icloud, ...)
    # work on cal commands the same way they do on message commands.
    wanted = set(_resolve_prefixes(source)) if source else None
    prefixes = []
    for pfx, cfg in all_sources.items():
        if cfg.get("provider") not in _CAL_PROVIDERS:
            continue
        if wanted is not None and pfx not in wanted:
            continue
        prefixes.append((pfx, cfg))

    if not prefixes:
        return []

    all_events: list[dict] = []
    errors: list[str] = []
    attempted = 0
    for pfx, cfg in prefixes:
        adapter = _make_adapter(pfx, cfg)
        if adapter is None:
            continue
        attempted += 1
        try:
            async with adapter:
                events = await adapter.list_events(
                    time_min=time_min, time_max=time_max,
                    count=count + _CAL_ALL_DAY_MARGIN,
                )
                all_events.extend(events)
        except Exception as exc:
            logger.warning("[%s] calendar adapter failed: %s", pfx, exc)
            errors.append(f"[{pfx}] {exc}")

    if source and attempted and len(errors) == attempted:
        raise RuntimeError("; ".join(errors))

    zone = tz or display_tzinfo()
    all_events = _cal_trim_all_day(all_events, time_min, time_max, zone)

    # Sort by the day the reader sees, all-day events first within that day,
    # then timed events by their UTC instant.  A plain string sort would put
    # a 00:30+01:00 event (stored as the previous UTC day) ahead of the
    # all-day entry it should follow.
    all_events.sort(key=lambda e: _cal_sort_key(e, zone))
    return all_events[:count]


# Adapters enforce `count` themselves, but _cal_trim_all_day can drop a
# boundary all-day event afterwards — so `cal next -n 5` could return four.
# Ask each source for a small margin; only events straddling the window's
# edges are ever discarded, so a couple of spares is enough.
_CAL_ALL_DAY_MARGIN = 2


def _cal_display_date(value: str, zone: "tzinfo") -> str:
    """The date a stored start/end lands on for the reader.

    Bare dates are all-day events and are already display dates — they are
    deliberately never shifted.  Instants are converted into *zone*.
    """
    if "T" not in value:
        return value
    dt = parse_utc(value)
    return dt.astimezone(zone).date().isoformat() if dt else value[:10]


def _cal_sort_key(event: dict, zone: "tzinfo") -> tuple[str, int, str]:
    start = event.get("start", "")
    return (_cal_display_date(start, zone), 0 if "T" not in start else 1, start)


def _cal_trim_all_day(
    events: list[dict], time_min: str, time_max: str, zone: "tzinfo",
) -> list[dict]:
    """Drop all-day events that fall outside the requested display dates.

    Calendar APIs select by overlap in absolute time, so a query bounded by
    display-zone midnights can return an adjacent day's all-day event when
    the calendar's own zone differs (a Tokyo March 11 all-day event begins
    during March 10 in New York).  All-day dates are never shifted, so the
    only correct filter is against the requested dates themselves.
    """
    lo = parse_utc(time_min)
    hi = parse_utc(time_max)
    if lo is None or hi is None:
        return events

    lo_date = lo.astimezone(zone).date()
    # Callers bound differently — midnight of the next day (cal today/week)
    # or 23:59:59 of the last day (cal range).  Stepping back an instant
    # yields the last date actually requested under either convention.
    last_date = (hi - timedelta(microseconds=1)).astimezone(zone).date()

    kept = []
    for event in events:
        start = event.get("start", "")
        if "T" in start or not start:
            kept.append(event)
            continue
        try:
            ev_start = date.fromisoformat(start)
            ev_end = date.fromisoformat(event.get("end") or start)
        except ValueError:
            kept.append(event)  # unparseable: degrade to today's behaviour
            continue
        # All-day ends are exclusive; a same-day end means a one-day event.
        ev_end = max(ev_end, ev_start + timedelta(days=1))
        if ev_start <= last_date and ev_end > lo_date:
            kept.append(event)
    return kept


def _cal_time_bounds(
    day_offset: int = 0, days: int = 1, tz: "tzinfo | None" = None,
) -> tuple[str, str]:
    """Compute time_min and time_max (UTC ISO) for calendar queries.

    Day boundaries are the reader's midnights — computed in the display
    timezone, then expressed in UTC like every other timestamp ts4k moves.
    """
    zone = tz or display_tzinfo()
    now = datetime.now(zone)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
    end = start + timedelta(days=days)
    return (
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
    )


async def cal_today(source: str | None, fmt: str, ref_table: RefTable | None = None) -> CommandResult:
    """Today's calendar events."""
    tz = display_tzinfo()
    time_min, time_max = _cal_time_bounds(day_offset=0, days=1, tz=tz)
    events = await _cal_fetch_events(source, time_min, time_max, tz=tz)
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=False, tz=tz)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_tomorrow(source: str | None, fmt: str, ref_table: RefTable | None = None) -> CommandResult:
    """Tomorrow's calendar events."""
    tz = display_tzinfo()
    time_min, time_max = _cal_time_bounds(day_offset=1, days=1, tz=tz)
    events = await _cal_fetch_events(source, time_min, time_max, tz=tz)
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=False, tz=tz)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_week(source: str | None, fmt: str, ref_table: RefTable | None = None) -> CommandResult:
    """This week's calendar events (Mon-Sun)."""
    tz = display_tzinfo()
    # Compute actual Monday-Sunday range for current week
    now = datetime.now(tz)
    time_min, time_max = _cal_time_bounds(day_offset=-now.weekday(), days=7, tz=tz)
    events = await _cal_fetch_events(source, time_min, time_max, tz=tz)
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=False, tz=tz)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_next(source: str | None, count: int, fmt: str, ref_table: RefTable | None = None) -> CommandResult:
    """Next N events from now, any timeframe."""
    tz = display_tzinfo()
    # Look ahead 365 days max, collapse recurring
    time_min, _ = _cal_time_bounds(day_offset=0, days=1, tz=tz)
    _, time_max = _cal_time_bounds(day_offset=0, days=365, tz=tz)
    events = await _cal_fetch_events(source, time_min, time_max, count=count, tz=tz)
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=True, tz=tz)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_range(
    source: str | None, from_date: str, to_date: str, fmt: str,
    ref_table: RefTable | None = None,
) -> CommandResult:
    """Events in an arbitrary date range."""
    tz = display_tzinfo()
    start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=tz)
    end = datetime.strptime(to_date, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=tz,
    )
    collapse = (end - start).days > 7
    events = await _cal_fetch_events(
        source,
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
        tz=tz,
    )
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=collapse, tz=tz)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_event(
    ref_or_id: str, source: str | None, fmt: str,
    ref_table: RefTable | None = None,
) -> str:
    """Full detail for a single event."""
    from ts4k.state import sources as src_mod

    # Resolve ref to event ID
    event_id = ref_or_id
    if ref_table is not None and (ref_or_id.isdigit() or ref_or_id.startswith("#")):
        resolved = ref_table.resolve(ref_or_id)
        if resolved is not None:
            event_id = resolved

    # Find the right adapter from the prefix
    prefix = event_id.split(":")[0] if ":" in event_id else source
    all_sources = src_mod.list_all()
    cfg = all_sources.get(prefix)
    if not cfg or cfg.get("provider") not in _CAL_PROVIDERS:
        return f"Error: no calendar source with prefix '{prefix}'"

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return f"Error: could not create adapter for '{prefix}'"

    async with adapter:
        event = await adapter.read_event(event_id)

    ref_num = int(ref_or_id) if ref_or_id.isdigit() else 0
    return format_event_detail(event, ref=ref_num, fmt=fmt, tz=display_tzinfo())


async def cal_list_calendars(email: str, config_dir: Path | None = None) -> list[dict]:
    """List available calendars for a Google account (non-interactive, for setup wizard)."""
    config = GcalAdapterConfig(
        email=email, calendar_id="primary", calendar_name="",
        timezone="UTC", config_dir=config_dir, level="readonly",
    )
    adapter = GcalAdapter(config, prefix="_setup")
    async with adapter:
        return await adapter.list_calendars()


async def cal_list_o365_calendars(
    email: str, client_id: str, tenant_id: str, config_dir: Path | None = None,
) -> list[dict]:
    """List available calendars for an O365 account (non-interactive, for setup wizard)."""
    config = O365CalAdapterConfig(
        email=email, client_id=client_id, tenant_id=tenant_id,
        calendar_id="default", calendar_name="",
        timezone="UTC", config_dir=config_dir, level="readonly",
    )
    adapter = O365CalAdapter(config, prefix="_setup")
    async with adapter:
        return await adapter.list_calendars()


async def cal_list_caldav_calendars(
    email: str, config_dir: Path | None = None,
) -> list[dict]:
    """List available calendars for a CalDAV account (non-interactive, for setup)."""
    from ts4k.auth.caldav import ICLOUD_CALDAV_URL

    config = CaldavAdapterConfig(
        email=email, server_url=ICLOUD_CALDAV_URL, calendar_id="",
        calendar_name="", timezone="UTC", config_dir=config_dir,
        level="readonly",
    )
    adapter = CaldavAdapter(config, prefix="_setup")
    async with adapter:
        return await adapter.list_calendars()


async def cal_create(
    source: str, title: str, start: str, end: str,
    description: str | None, location: str | None,
    attendees: list[str] | None,
    ref_table: RefTable | None = None,
) -> str:
    """Create a calendar event."""
    from ts4k.state import sources as src_mod

    cfg = src_mod.list_all().get(source)
    if not cfg or cfg.get("provider") not in _CAL_PROVIDERS:
        return f"Error: '{source}' is not a calendar source"

    adapter = _make_adapter(source, cfg)
    if adapter is None:
        return f"Error: could not create adapter for '{source}'"

    async with adapter:
        event = await adapter.create_event(
            title=title, start=start, end=end,
            description=description, location=location, attendees=attendees,
        )

    return f"Created: {event['title']} ({event['id']})"


async def cal_update(ref_or_id: str, source: str | None, ref_table: RefTable | None = None, **fields) -> str:
    """Update a calendar event."""
    from ts4k.state import sources as src_mod

    event_id = ref_or_id
    if ref_table is not None and (ref_or_id.isdigit() or ref_or_id.startswith("#")):
        resolved = ref_table.resolve(ref_or_id)
        if resolved is not None:
            event_id = resolved

    prefix = event_id.split(":")[0] if ":" in event_id else source
    cfg = src_mod.list_all().get(prefix)
    if not cfg or cfg.get("provider") not in _CAL_PROVIDERS:
        return f"Error: no calendar source with prefix '{prefix}'"

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return f"Error: could not create adapter for '{prefix}'"

    async with adapter:
        event = await adapter.update_event(event_id, **fields)

    return f"Updated: {event['title']} ({event['id']})"


async def cal_rsvp(ref_or_id: str, source: str | None, status: str, ref_table: RefTable | None = None) -> str:
    """RSVP to a calendar event."""
    from ts4k.state import sources as src_mod

    event_id = ref_or_id
    if ref_table is not None and (ref_or_id.isdigit() or ref_or_id.startswith("#")):
        resolved = ref_table.resolve(ref_or_id)
        if resolved is not None:
            event_id = resolved

    prefix = event_id.split(":")[0] if ":" in event_id else source
    cfg = src_mod.list_all().get(prefix)
    if not cfg or cfg.get("provider") not in _CAL_PROVIDERS:
        return f"Error: no calendar source with prefix '{prefix}'"

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return f"Error: could not create adapter for '{prefix}'"

    try:
        async with adapter:
            event = await adapter.rsvp(event_id, status=status)
    except (ValueError, RuntimeError) as e:
        return f"Error: {e}"

    return f"RSVP {status}: {event['title']} ({event['id']})"

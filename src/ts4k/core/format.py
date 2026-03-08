"""ts4k formatters — pipe-delimited, JSON, and XML output.

Three formats optimised for different consumers:

* **pipe** (default for listings): most token-compact for LLMs.
* **json**: for programmatic / tool use.
* **xml**: fastest LLM parsing, attribute-heavy.

Target: 60%+ byte savings vs raw JSON pretty-print for listings.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape, quoteattr as xml_quoteattr


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def format_listing(
    messages: list[dict],
    fmt: str = "pipe",
    ref_map: dict[str, int] | None = None,
) -> str:
    """Format a list of message-header dicts.

    Each dict should have at least: ``source``, ``from``, ``subject``,
    ``date``, ``id``.  ``body`` is used only for size estimation.

    *fmt*: ``'pipe'``, ``'json'``, ``'xml'``.
    *ref_map*: ``{full_id: ref_num}`` — when provided, pipe format uses
    ``#N`` short refs instead of full IDs, and compact timestamps.
    """
    fmt = _resolve_fmt(fmt)

    if fmt == "pipe":
        return _listing_pipe(messages, ref_map=ref_map)
    elif fmt == "json":
        return _listing_json(messages)
    elif fmt == "xml":
        return _listing_xml(messages)
    else:
        raise ValueError(f"Unknown format: {fmt!r}")


def format_message(message: dict, fmt: str = "pipe") -> str:
    """Format a single message with body.

    *fmt*: ``'pipe'``, ``'json'``, ``'xml'``.
    """
    fmt = _resolve_fmt(fmt)

    if fmt == "pipe":
        return _message_pipe(message)
    elif fmt == "json":
        return _message_json(message)
    elif fmt == "xml":
        return _message_xml(message)
    else:
        raise ValueError(f"Unknown format: {fmt!r}")


def format_thread(thread: dict, fmt: str = "pipe") -> str:
    """Format a thread with messages.

    *thread* must have ``thread_id``, ``subject``, ``message_count``,
    ``messages`` (list of message dicts with ``from``, ``date``, ``body``).

    *fmt*: ``'pipe'``, ``'json'``, ``'xml'``.
    """
    fmt = _resolve_fmt(fmt)

    if fmt == "pipe":
        return _thread_pipe(thread)
    elif fmt == "json":
        return _thread_json(thread)
    elif fmt == "xml":
        return _thread_xml(thread)
    else:
        raise ValueError(f"Unknown format: {fmt!r}")


def format_overview(data: dict, fmt: str = "pipe") -> str:
    """Format an overview data dict.

    *data* must have a ``level`` key (``'top'``, ``'source'``, ``'contact'``).

    *fmt*: ``'pipe'``, ``'json'``, ``'xml'``.
    """
    fmt = _resolve_fmt(fmt)

    if fmt == "pipe":
        return _overview_pipe(data)
    elif fmt == "json":
        return _overview_json(data)
    elif fmt == "xml":
        return _overview_xml(data)
    else:
        raise ValueError(f"Unknown format: {fmt!r}")


def format_mailbox_stats(
    stats: dict[str, dict | None],
    fmt: str = "pipe",
) -> str:
    """Format mailbox stats from multiple sources.

    *stats*: ``{prefix: {"provider": "...", "labels": [...]} | None}``.
    ``None`` means the source was unreachable.

    *fmt*: ``'pipe'``, ``'json'``, ``'xml'``.
    """
    fmt = _resolve_fmt(fmt)

    if fmt == "pipe":
        return _mailbox_stats_pipe(stats)
    elif fmt == "json":
        return _mailbox_stats_json(stats)
    elif fmt == "xml":
        return _mailbox_stats_xml(stats)
    else:
        raise ValueError(f"Unknown format: {fmt!r}")


def estimate_size(text_or_bytes: str | int) -> str:
    """Human-readable size estimate.

    Accepts a string (measures UTF-8 byte length) or an int (byte count).
    Returns e.g. ``'0b'``, ``'500b'``, ``'2kb'``, ``'1mb'``.
    """
    if not text_or_bytes:
        return "0b"

    n = text_or_bytes if isinstance(text_or_bytes, int) else len(text_or_bytes.encode("utf-8"))

    if n < 1000:
        return f"{n}b"
    elif n < 1_000_000:
        kb = round(n / 1024)
        return f"{kb}kb" if kb > 0 else "1kb"
    else:
        mb = round(n / (1024 * 1024))
        return f"{mb}mb" if mb > 0 else "1mb"


# ---------------------------------------------------------------------------
# Format alias resolution
# ---------------------------------------------------------------------------


_FMT_ALIASES: dict[str, str] = {
    "p": "pipe",
    "pipe": "pipe",
    "j": "json",
    "json": "json",
    "x": "xml",
    "xml": "xml",
}


def _resolve_fmt(fmt: str) -> str:
    """Resolve a format alias to a canonical name."""
    resolved = _FMT_ALIASES.get(fmt.lower().strip())
    if resolved is None:
        raise ValueError(
            f"Unknown format {fmt!r}. Choose from: pipe (p), json (j), xml (x)"
        )
    return resolved


# ---------------------------------------------------------------------------
# Helpers — extract common fields with safe defaults
# ---------------------------------------------------------------------------


def _source(msg: dict) -> str:
    """Extract source prefix from message dict.

    Tries ``source`` key first, then infers from ``id`` prefix (e.g. ``g:``).
    """
    if "source" in msg:
        return msg["source"]
    msg_id = msg.get("id", "")
    if ":" in msg_id:
        return msg_id.split(":")[0]
    return ""


def _size(msg: dict) -> str:
    """Estimate size from body or return pre-computed size."""
    if "size" in msg:
        return msg["size"]
    return estimate_size(msg.get("body", ""))


# ---------------------------------------------------------------------------
# Pipe-delimited formatters
# ---------------------------------------------------------------------------


def _listing_pipe(
    messages: list[dict],
    ref_map: dict[str, int] | None = None,
) -> str:
    """Pipe-delimited listing — most compact format.

    When *ref_map* is provided, uses short ``#N`` refs and compact timestamps
    with date-header grouping.  Otherwise falls back to the legacy format with
    full IDs and ISO timestamps.
    """
    if ref_map is not None:
        return _listing_pipe_refs(messages, ref_map)
    return _listing_pipe_legacy(messages)


def _listing_pipe_legacy(messages: list[dict]) -> str:
    """Legacy pipe listing with full IDs and ISO timestamps."""
    has_snippets = any(msg.get("snippet") for msg in messages)
    if has_snippets:
        lines = ["SOURCE|FROM|SUBJECT|DATE|ID|SIZE|SNIPPET"]
        for msg in messages:
            snippet = msg.get("snippet", "").strip()
            if len(snippet) > 80:
                snippet = snippet[:77].rstrip() + "..."
            lines.append(
                f"{_source(msg)}|{msg.get('from', '')}|{msg.get('subject', '')}"
                f"|{msg.get('date', '')}|{msg.get('id', '')}|{_size(msg)}|{snippet}"
            )
    else:
        lines = ["SOURCE|FROM|SUBJECT|DATE|ID|SIZE"]
        for msg in messages:
            lines.append(
                f"{_source(msg)}|{msg.get('from', '')}|{msg.get('subject', '')}"
                f"|{msg.get('date', '')}|{msg.get('id', '')}|{_size(msg)}"
            )
    return "\n".join(lines)


def _listing_pipe_refs(messages: list[dict], ref_map: dict[str, int]) -> str:
    """Pipe listing with short refs, compact timestamps, and date-header grouping."""
    precision = _detect_precision(messages)
    has_snippets = any(msg.get("snippet") for msg in messages)

    # Group by date for date-header grouping (only when messages span multiple days)
    use_headers = precision != "time"
    groups = _group_by_date(messages, precision) if use_headers else None

    lines: list[str] = []

    if has_snippets:
        lines.append("#|SOURCE|FROM|SUBJECT|DATE|SIZE|SNIPPET")
    else:
        lines.append("#|SOURCE|FROM|SUBJECT|DATE|SIZE")

    if groups:
        for date_label, group_msgs in groups:
            lines.append(f"--- {date_label} ---")
            for msg in group_msgs:
                ref = ref_map.get(msg.get("id", ""), 0)
                ts = _compact_ts(msg.get("date", ""), "time")
                row = f"#{ref}|{_source(msg)}|{msg.get('from', '')}|{msg.get('subject', '')}|{ts}|{_size(msg)}"
                if has_snippets:
                    snippet = msg.get("snippet", "").strip()
                    if len(snippet) > 80:
                        snippet = snippet[:77].rstrip() + "..."
                    row += f"|{snippet}"
                lines.append(row)
    else:
        for msg in messages:
            ref = ref_map.get(msg.get("id", ""), 0)
            ts = _compact_ts(msg.get("date", ""), precision)
            row = f"#{ref}|{_source(msg)}|{msg.get('from', '')}|{msg.get('subject', '')}|{ts}|{_size(msg)}"
            if has_snippets:
                snippet = msg.get("snippet", "").strip()
                if len(snippet) > 80:
                    snippet = snippet[:77].rstrip() + "..."
                row += f"|{snippet}"
            lines.append(row)

    return "\n".join(lines)


def _message_pipe(message: dict) -> str:
    """Pipe-delimited single message with body.

    Header line then blank line then body.
    """
    header = (
        f"{_source(message)}|{message.get('from', '')}|{message.get('subject', '')}"
        f"|{message.get('date', '')}|{message.get('id', '')}|{_size(message)}"
    )
    body = message.get("body", "")
    parts = [header]
    if body:
        parts.append("")
        parts.append(body)
    return "\n".join(parts)


def _thread_pipe(thread: dict) -> str:
    """Pipe-delimited thread.

    Thread header, then each message separated by a ``---`` divider.
    """
    lines = [
        f"THREAD|{thread.get('thread_id', '')}|{thread.get('subject', '')}"
        f"|{thread.get('message_count', 0)} msgs"
    ]

    for msg in thread.get("messages", []):
        lines.append("---")
        lines.append(
            f"{msg.get('from', '')}|{msg.get('date', '')}"
        )
        body = msg.get("body", "")
        if body:
            lines.append(body)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Compact timestamp helpers
# ---------------------------------------------------------------------------

# Month abbreviations (1-indexed: _MONTHS[1] = "Jan")
_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _parse_iso(iso_str: str) -> datetime | None:
    """Parse an ISO 8601 timestamp to datetime.  Returns None on failure."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _detect_precision(messages: list[dict]) -> str:
    """Determine the most compact timestamp precision for a message set.

    Returns ``'time'`` (same day), ``'day'`` (same year), or ``'year'`` (spans years).
    """
    dates: list[datetime] = []
    for msg in messages:
        dt = _parse_iso(msg.get("date", ""))
        if dt:
            dates.append(dt)

    if not dates:
        return "day"

    years = {d.year for d in dates}
    if len(years) > 1:
        return "year"

    day_keys = {(d.year, d.month, d.day) for d in dates}
    if len(day_keys) == 1:
        return "time"

    return "day"


def _compact_ts(iso_str: str, precision: str) -> str:
    """Format a single timestamp at the chosen precision.

    - ``'time'``: ``22:23``
    - ``'day'``: ``23Feb 22:23``
    - ``'year'``: ``23Feb25 22:23``
    """
    dt = _parse_iso(iso_str)
    if dt is None:
        return iso_str  # fallback: return as-is

    time_part = f"{dt.hour:02d}:{dt.minute:02d}"
    if precision == "time":
        return time_part
    month = _MONTHS[dt.month]
    if precision == "day":
        return f"{dt.day}{month} {time_part}"
    # year
    yr = dt.year % 100
    return f"{dt.day}{month}{yr:02d} {time_part}"


def _date_label(dt: datetime, precision: str) -> str:
    """Date header label for grouping rows."""
    month = _MONTHS[dt.month]
    if precision == "year":
        yr = dt.year % 100
        return f"{dt.day}{month}{yr:02d}"
    return f"{dt.day}{month}"


def _group_by_date(
    messages: list[dict], precision: str
) -> list[tuple[str, list[dict]]]:
    """Group messages by date for date-header output.

    Returns ``[(date_label, [messages]), ...]`` in message order.
    """
    groups: list[tuple[str, list[dict]]] = []
    current_label: str | None = None
    current_group: list[dict] = []

    for msg in messages:
        dt = _parse_iso(msg.get("date", ""))
        label = _date_label(dt, precision) if dt else "?"
        if label != current_label:
            if current_group:
                groups.append((current_label or "?", current_group))
            current_label = label
            current_group = [msg]
        else:
            current_group.append(msg)

    if current_group:
        groups.append((current_label or "?", current_group))

    return groups


# ---------------------------------------------------------------------------
# JSON formatters
# ---------------------------------------------------------------------------


def _listing_json(messages: list[dict]) -> str:
    """Compact JSON array — no pretty-printing."""
    items = []
    for msg in messages:
        items.append({
            "source": _source(msg),
            "from": msg.get("from", ""),
            "subject": msg.get("subject", ""),
            "date": msg.get("date", ""),
            "id": msg.get("id", ""),
            "size": _size(msg),
        })
    return json.dumps(items, separators=(",", ":"))


def _message_json(message: dict) -> str:
    """Compact JSON for a single message with body."""
    obj: dict = {
        "source": _source(message),
        "from": message.get("from", ""),
        "subject": message.get("subject", ""),
        "date": message.get("date", ""),
        "id": message.get("id", ""),
        "size": _size(message),
        "body": message.get("body", ""),
    }
    if message.get("to"):
        obj["to"] = message["to"]
    if message.get("cc"):
        obj["cc"] = message["cc"]
    if message.get("attachments"):
        obj["attachments"] = message["attachments"]
    return json.dumps(obj, separators=(",", ":"))


def _thread_json(thread: dict) -> str:
    """Compact JSON for a thread."""
    obj = {
        "thread_id": thread.get("thread_id", ""),
        "subject": thread.get("subject", ""),
        "message_count": thread.get("message_count", 0),
        "messages": [],
    }
    for msg in thread.get("messages", []):
        m: dict = {
            "from": msg.get("from", ""),
            "date": msg.get("date", ""),
            "body": msg.get("body", ""),
        }
        if msg.get("subject"):
            m["subject"] = msg["subject"]
        obj["messages"].append(m)
    return json.dumps(obj, separators=(",", ":"))


# ---------------------------------------------------------------------------
# XML formatters
# ---------------------------------------------------------------------------


def _listing_xml(messages: list[dict]) -> str:
    """XML listing — attributes for headers, self-closing tags.

    ::

        <msgs>
        <m id="g:abc" from="alice@acme.com" subject="Meeting" date="..." size="2kb"/>
        </msgs>
    """
    lines = ["<msgs>"]
    for msg in messages:
        attrs = (
            f' id={xml_quoteattr(msg.get("id", ""))}'
            f' from={xml_quoteattr(msg.get("from", ""))}'
            f' subject={xml_quoteattr(msg.get("subject", ""))}'
            f' date={xml_quoteattr(msg.get("date", ""))}'
            f' size={xml_quoteattr(_size(msg))}'
        )
        lines.append(f"<m{attrs}/>")
    lines.append("</msgs>")
    return "\n".join(lines)


def _message_xml(message: dict) -> str:
    """XML single message — attributes for headers, body as element content.

    ::

        <e id="g:abc" from="alice@acme.com" subject="Meeting" date="2026-02-19">
        Body text here.
        </e>
    """
    attrs = (
        f' id={xml_quoteattr(message.get("id", ""))}'
        f' from={xml_quoteattr(message.get("from", ""))}'
        f' subject={xml_quoteattr(message.get("subject", ""))}'
        f' date={xml_quoteattr(message.get("date", ""))}'
        f' size={xml_quoteattr(_size(message))}'
    )
    body = xml_escape(message.get("body", ""))
    return f"<e{attrs}>\n{body}\n</e>"


def _thread_xml(thread: dict) -> str:
    """XML thread — wrapping element with nested messages.

    ::

        <thread id="g:abc" subject="Meeting" count="3">
        <m from="alice@acme.com" date="...">
        Body text here.
        </m>
        </thread>
    """
    t_attrs = (
        f' id={xml_quoteattr(thread.get("thread_id", ""))}'
        f' subject={xml_quoteattr(thread.get("subject", ""))}'
        f' count={xml_quoteattr(str(thread.get("message_count", 0)))}'
    )
    lines = [f"<thread{t_attrs}>"]
    for msg in thread.get("messages", []):
        m_attrs = (
            f' from={xml_quoteattr(msg.get("from", ""))}'
            f' date={xml_quoteattr(msg.get("date", ""))}'
        )
        body = xml_escape(msg.get("body", ""))
        lines.append(f"<m{m_attrs}>")
        lines.append(body)
        lines.append("</m>")
    lines.append("</thread>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Overview formatters
# ---------------------------------------------------------------------------


def _overview_pipe(data: dict) -> str:
    """Pipe-delimited overview output."""
    level = data.get("level", "top")
    lines: list[str] = []

    if level == "top":
        total = data.get("total", 0)
        src_count = data.get("source_count", 0)
        lines.append(f"Overview: {src_count} sources, {total} messages cached")
        for src in data.get("sources", []):
            top = ", ".join(
                f"{s['name']}({s['count']})" for s in src.get("top_senders", [])
            )
            date_range = ""
            if src.get("date_start"):
                date_range = f"{src['date_start']}..{src['date_end']}"
            lines.append(
                f"{src['prefix']}|{src['label']}|{src['count']} msgs"
                f"|{date_range}|top: {top}"
            )
        lines.append("---")
        lines.append("Drill: ts4k o --source <prefix> | ts4k o --contact <name>")

    elif level == "source":
        prefix = data.get("prefix", "")
        label = data.get("label", prefix)
        total = data.get("total", 0)
        date_range = ""
        if data.get("date_start"):
            date_range = f", {data['date_start']}..{data['date_end']}"
        lines.append(f"Overview: {prefix} ({label}), {total} messages{date_range}")

        lines.append("TOP_SENDERS:")
        for s in data.get("top_senders", []):
            lines.append(f"{s['name']}|{s['count']} msgs")

        top_threads = data.get("top_threads", [])
        if top_threads:
            lines.append("TOP_THREADS:")
            for t in top_threads:
                lines.append(f"{t.get('subject', '')}|{t['count']} msgs|{t.get('id', '')}")

        lines.append("---")
        lines.append("Drill: ts4k o --contact <name> | ts4k g <msg_id>")

    elif level == "contact":
        contact = data.get("contact", "")
        total = data.get("total", 0)
        src_count = data.get("source_count", 0)
        lines.append(f"Overview: {contact}, {total} messages across {src_count} sources")

        for src in data.get("sources", []):
            date_range = ""
            if src.get("date_start"):
                date_range = f"|{src['date_start']}..{src['date_end']}"
            lines.append(
                f"{src['prefix']}|{src['label']}|{src['count']} msgs{date_range}"
            )

        periods = data.get("periods", [])
        if periods:
            lines.append("PERIODS:")
            for p in periods:
                lines.append(f"{p['period']}|{p['count']}")

        lines.append("---")
        lines.append(f"Drill: ts4k o --contact {contact} --period <period>")

    return "\n".join(lines)


def _overview_json(data: dict) -> str:
    """Compact JSON overview."""
    return json.dumps(data, separators=(",", ":"))


def _overview_xml(data: dict) -> str:
    """XML overview — attribute-heavy."""
    level = data.get("level", "top")
    lines: list[str] = []

    if level == "top":
        lines.append(
            f'<overview level="top" total="{data.get("total", 0)}"'
            f' sources="{data.get("source_count", 0)}">'
        )
        for src in data.get("sources", []):
            date_range = src.get("date_start", "") + ".." + src.get("date_end", "")
            attrs = (
                f' prefix={xml_quoteattr(src["prefix"])}'
                f' label={xml_quoteattr(src["label"])}'
                f' count="{src["count"]}"'
                f' range={xml_quoteattr(date_range)}'
            )
            senders = " ".join(
                f"{s['name']}({s['count']})" for s in src.get("top_senders", [])
            )
            lines.append(f"<src{attrs} top={xml_quoteattr(senders)}/>")
        lines.append("</overview>")

    elif level == "source":
        lines.append(
            f'<overview level="source"'
            f' prefix={xml_quoteattr(data.get("prefix", ""))}'
            f' label={xml_quoteattr(data.get("label", ""))}'
            f' total="{data.get("total", 0)}">'
        )
        for s in data.get("top_senders", []):
            lines.append(
                f'<sender name={xml_quoteattr(s["name"])} count="{s["count"]}"/>'
            )
        for t in data.get("top_threads", []):
            lines.append(
                f'<thread subject={xml_quoteattr(t.get("subject", ""))}'
                f' count="{t["count"]}"'
                f' id={xml_quoteattr(t.get("id", ""))}/>',
            )
        lines.append("</overview>")

    elif level == "contact":
        lines.append(
            f'<overview level="contact"'
            f' name={xml_quoteattr(data.get("contact", ""))}'
            f' total="{data.get("total", 0)}">'
        )
        for src in data.get("sources", []):
            lines.append(
                f'<src prefix={xml_quoteattr(src["prefix"])}'
                f' label={xml_quoteattr(src["label"])}'
                f' count="{src["count"]}"/>'
            )
        for p in data.get("periods", []):
            lines.append(
                f'<period name={xml_quoteattr(p["period"])} count="{p["count"]}"/>'
            )
        lines.append("</overview>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mailbox stats formatters
# ---------------------------------------------------------------------------


def _mailbox_stats_pipe(stats: dict[str, dict | None]) -> str:
    lines: list[str] = []
    for prefix, data in sorted(stats.items()):
        if data is None:
            lines.append(f"Mailbox ({prefix}): (offline)")
            continue
        provider = data.get("provider", prefix)
        lines.append(f"Mailbox ({prefix}, {provider}):")
        lines.append("  LABEL|TOTAL|UNREAD")
        for label in data.get("labels", []):
            lines.append(
                f"  {label['name']}|{label['total']}|{label['unread']}"
            )
    return "\n".join(lines)


def _mailbox_stats_json(stats: dict[str, dict | None]) -> str:
    mailbox_list = []
    for prefix, data in sorted(stats.items()):
        if data is None:
            mailbox_list.append({"source": prefix, "error": "offline"})
        else:
            mailbox_list.append({
                "source": prefix,
                "provider": data.get("provider", prefix),
                "labels": data.get("labels", []),
            })
    return json.dumps({"mailbox": mailbox_list}, separators=(",", ":"))


def _mailbox_stats_xml(stats: dict[str, dict | None]) -> str:
    lines = ["<mailbox>"]
    for prefix, data in sorted(stats.items()):
        if data is None:
            lines.append(
                f'<src prefix={xml_quoteattr(prefix)} error="offline"/>'
            )
            continue
        provider = data.get("provider", prefix)
        lines.append(
            f'<src prefix={xml_quoteattr(prefix)}'
            f' provider={xml_quoteattr(provider)}>'
        )
        for label in data.get("labels", []):
            lines.append(
                f'<label name={xml_quoteattr(label["name"])}'
                f' total="{label["total"]}"'
                f' unread="{label["unread"]}"/>'
            )
        lines.append("</src>")
    lines.append("</mailbox>")
    return "\n".join(lines)

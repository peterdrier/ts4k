"""Timezone helpers — UTC internally, local only at display.

ts4k stores and compares every timestamp in UTC.  Adapters convert at the
edge (:func:`to_utc_iso`), the pipeline sorts the resulting ISO strings
lexicographically — which is chronologically correct precisely *because*
every string carries the same ``+00:00`` offset — and the format layer
converts back to a wall clock exactly once, at render time.

The display timezone is **global**, not per-source: a merged agenda drawn
from calendars configured in different zones must not mix offsets, or the
listing it produces is unreadable.  Resolution order:

1. ``TS4K_TIMEZONE`` env var
2. ``timezone`` in ``~/.config/ts4k/settings.json``
3. the machine's own zone

All-day events are the exception to the rule: they are *dates*, not
instants, and stay bare ``YYYY-MM-DD`` strings the whole way through.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

_ENV_VAR = "TS4K_TIMEZONE"


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def tzinfo_for(name: str | tzinfo | None) -> tzinfo:
    """Resolve an IANA name to a tzinfo, falling back to UTC for unknown zones."""
    if isinstance(name, tzinfo):
        return name
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def to_utc_iso(value: str, assume: str | tzinfo = timezone.utc) -> str:
    """Convert an ISO-8601 timestamp to a UTC ISO string.

    Values without an offset are floating times — they are interpreted in
    *assume* (a source's configured zone) before conversion.  Anything that
    does not parse is returned unchanged, so a surprising API payload
    degrades to today's behaviour instead of vanishing.
    """
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tzinfo_for(assume))
    return dt.astimezone(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime | None:
    """Parse a stored timestamp as an aware UTC datetime, or None if malformed.

    Naive values are read as UTC — that is what "stored" means here.
    """
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Display timezone
# ---------------------------------------------------------------------------


def display_tzinfo() -> tzinfo:
    """The single global display timezone: env var, then config, then machine."""
    name = os.environ.get(_ENV_VAR)
    if not name:
        from ts4k.state import settings

        name = settings.get_timezone()
    if name:
        return tzinfo_for(name)
    return system_tzinfo()


def system_tzinfo() -> tzinfo:
    """The machine's zone, as a DST-aware ZoneInfo where one can be named."""
    name = os.environ.get("TZ") or _localtime_key()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    # No IANA name available (Windows, or an unreadable zoneinfo tree): the
    # current local offset, which is right except across a DST boundary.
    return datetime.now().astimezone().tzinfo or timezone.utc


def _localtime_key() -> str | None:
    """IANA key behind /etc/localtime, when it points into the zoneinfo tree."""
    try:
        parts = Path("/etc/localtime").resolve().parts
    except OSError:
        return None
    if "zoneinfo" not in parts:
        return None
    return "/".join(parts[parts.index("zoneinfo") + 1:]) or None

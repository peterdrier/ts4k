"""Message cache — local store for normalized messages.

Stores cached messages under ``~/.config/ts4k/cache/``:

- ``index.json`` — header-only dict keyed by prefixed message ID.
  Supports fast listing/filtering without loading bodies.
- ``bodies/`` — one ``.json`` file per message with the full normalized body.
  Named by URL-safe msg ID (colons replaced with underscores).

Only adapters that make network round-trips (Gmail, O365) participate.
WhatsApp reads from a local SQLite DB and is excluded.

The index carries a ``_meta.schema_version`` field.  When normalize logic
or cache format changes, bump ``SCHEMA_VERSION``.  Entries written under
an older version are treated as cache misses.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_CACHE_DIR = _CONFIG_DIR / "cache"
_INDEX_FILE = _CACHE_DIR / "index.json"
_BODIES_DIR = _CACHE_DIR / "bodies"

SCHEMA_VERSION = 1

# Sources that participate in caching (network-heavy adapters only).
CACHEABLE_SOURCES = {"g", "o"}

# Minimum free disk space required for preload operations.
MIN_FREE_BYTES = 5 * 1024 ** 3  # 5 GB


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load_index() -> dict[str, Any]:
    """Load the index from disk, or return empty structure."""
    if not _INDEX_FILE.exists():
        return {"_meta": {"schema_version": SCHEMA_VERSION}, "messages": {}}
    try:
        data = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "messages" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"_meta": {"schema_version": SCHEMA_VERSION}, "messages": {}}


def _save_index(data: dict[str, Any]) -> None:
    """Persist the index to disk."""
    data.setdefault("_meta", {})["schema_version"] = SCHEMA_VERSION
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _INDEX_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _body_path(msg_id: str) -> Path:
    """Return the file path for a cached body, using URL-safe ID."""
    safe = msg_id.replace(":", "_")
    return _BODIES_DIR / f"{safe}.json"


def _is_current(entry: dict) -> bool:
    """True if *entry* was written under the current schema version."""
    return entry.get("_schema_version", 0) >= SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------


def store_header(msg_id: str, header: dict) -> None:
    """Cache a message header (no body).

    *header* should contain at least ``from``, ``subject``, ``date``,
    ``source``.  The ``body`` key, if present, is stripped.
    """
    source = header.get("source", "")
    if source and source not in CACHEABLE_SOURCES:
        return

    index = _load_index()
    entry = {k: v for k, v in header.items() if k != "body"}
    entry["_schema_version"] = SCHEMA_VERSION
    entry["_cached_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    index["messages"][msg_id] = entry
    _save_index(index)


def store_body(msg_id: str, body: str) -> None:
    """Cache a message body to a separate file."""
    _BODIES_DIR.mkdir(parents=True, exist_ok=True)
    path = _body_path(msg_id)
    path.write_text(json.dumps({"body": body}) + "\n", encoding="utf-8")


def store_message(msg_id: str, msg: dict) -> None:
    """Cache a full message (header + body in one call)."""
    store_header(msg_id, msg)
    body = msg.get("body")
    if body:
        store_body(msg_id, body)


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def get_header(msg_id: str) -> dict | None:
    """Return the cached header for *msg_id*, or ``None`` on miss/stale."""
    index = _load_index()
    entry = index["messages"].get(msg_id)
    if entry is None or not _is_current(entry):
        return None
    return entry


def get_body(msg_id: str) -> str | None:
    """Return the cached body for *msg_id*, or ``None`` if not cached."""
    path = _body_path(msg_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("body")
    except (json.JSONDecodeError, OSError):
        return None


def get_message(msg_id: str) -> dict | None:
    """Return full cached message (header + body), or ``None`` on miss."""
    header = get_header(msg_id)
    if header is None:
        return None
    body = get_body(msg_id)
    if body is not None:
        return {**header, "body": body}
    return header


def has(msg_id: str) -> bool:
    """True if *msg_id* is in the cache and current schema."""
    return get_header(msg_id) is not None


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------


def list_headers(
    source: str | None = None,
    since: str | None = None,
    contact: str | None = None,
) -> list[dict]:
    """Return cached headers matching the filters.

    Parameters
    ----------
    source : str, optional
        Filter by source prefix (e.g. ``"g"``).
    since : str, optional
        ISO timestamp — only messages on or after this date.
    contact : str, optional
        Substring match on the ``from`` field (case-insensitive).
    """
    index = _load_index()
    results = []
    for msg_id, entry in index["messages"].items():
        if not _is_current(entry):
            continue
        if source and entry.get("source") != source:
            continue
        if since and entry.get("date", "") < since:
            continue
        if contact and contact.lower() not in entry.get("from", "").lower():
            continue
        results.append({**entry, "id": msg_id})
    return results


def count(source: str | None = None) -> int:
    """Return the number of current (non-stale) cached messages."""
    index = _load_index()
    total = 0
    for entry in index["messages"].values():
        if not _is_current(entry):
            continue
        if source and entry.get("source") != source:
            continue
        total += 1
    return total


def stats() -> dict[str, Any]:
    """Return cache statistics.

    Returns a dict with ``total``, ``by_source``, ``bodies``,
    ``index_bytes``, ``bodies_bytes``, ``stale``.
    """
    index = _load_index()
    messages = index.get("messages", {})

    by_source: dict[str, int] = {}
    stale = 0
    current = 0
    oldest: str | None = None
    newest: str | None = None

    for entry in messages.values():
        if not _is_current(entry):
            stale += 1
            continue
        current += 1
        src = entry.get("source", "?")
        by_source[src] = by_source.get(src, 0) + 1
        d = entry.get("date", "")
        if d:
            if oldest is None or d < oldest:
                oldest = d
            if newest is None or d > newest:
                newest = d

    # Disk sizes
    index_bytes = _INDEX_FILE.stat().st_size if _INDEX_FILE.exists() else 0
    bodies_bytes = 0
    bodies_count = 0
    if _BODIES_DIR.exists():
        for f in _BODIES_DIR.iterdir():
            if f.suffix == ".json":
                bodies_count += 1
                bodies_bytes += f.stat().st_size

    return {
        "total": current,
        "stale": stale,
        "by_source": by_source,
        "bodies": bodies_count,
        "index_bytes": index_bytes,
        "bodies_bytes": bodies_bytes,
        "oldest": oldest,
        "newest": newest,
        "schema_version": SCHEMA_VERSION,
    }


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


def clear(source: str | None = None, stale_only: bool = False) -> int:
    """Remove cached messages.

    Parameters
    ----------
    source : str, optional
        Only clear messages from this source. ``None`` means all.
    stale_only : bool
        If True, only remove entries below the current schema version.

    Returns the number of entries removed.
    """
    index = _load_index()
    messages = index.get("messages", {})
    to_remove: list[str] = []

    for msg_id, entry in messages.items():
        if stale_only and _is_current(entry):
            continue
        if source and entry.get("source") != source:
            continue
        to_remove.append(msg_id)

    for msg_id in to_remove:
        del messages[msg_id]
        body_file = _body_path(msg_id)
        if body_file.exists():
            body_file.unlink()

    _save_index(index)
    return len(to_remove)


def check_disk_space() -> bool:
    """Return ``True`` if the cache directory has >= 5 GB free."""
    target = _CACHE_DIR if _CACHE_DIR.exists() else _CONFIG_DIR
    if not target.exists():
        target = Path.home()
    free = shutil.disk_usage(target).free
    return free >= MIN_FREE_BYTES

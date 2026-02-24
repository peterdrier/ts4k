"""Watermark tracking — remember where we left off per source.

Stores a JSON file at ``~/.config/ts4k/watermarks.json`` with the last-seen
timestamp per source (``g``, ``w``, ``t``, etc.).  The ``whatsnew`` command
reads the watermark to build its query, then updates it after a successful
fetch.

File format::

    {
        "g": "2026-02-21T14:30:00Z",
        "w": "2026-02-20T09:00:00Z"
    }
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_WM_FILE = _CONFIG_DIR / "watermarks.json"


def _load() -> dict[str, str]:
    """Load watermarks from disk, or return empty dict."""
    if not _WM_FILE.exists():
        return {}
    try:
        return json.loads(_WM_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, str]) -> None:
    """Persist watermarks to disk."""
    from ts4k.state._io import safe_write_json
    safe_write_json(_WM_FILE, data)


def get(source: str) -> str | None:
    """Return the last-seen ISO timestamp for *source*, or ``None``."""
    return _load().get(source)


def update(source: str, timestamp: str | None = None) -> str:
    """Set the watermark for *source* to *timestamp* (default: now UTC).

    Returns the timestamp that was written.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _load()
    data[source] = timestamp
    _save(data)
    return timestamp


def clear(source: str | None = None) -> None:
    """Clear watermark for *source*, or all watermarks if *source* is ``None``."""
    if source is None:
        if _WM_FILE.exists():
            _WM_FILE.unlink()
        return
    data = _load()
    data.pop(source, None)
    _save(data)


def all() -> dict[str, str]:
    """Return all watermarks."""
    return _load()

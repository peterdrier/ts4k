"""Source configuration — maps user-chosen prefixes to provider accounts.

Stores a JSON file at ``~/.config/ts4k/sources.json``.  Each key is a
short prefix (user-chosen, any text) that identifies a specific account
on a specific provider.

File format::

    {
        "g": {
            "provider": "gmail",
            "email": "user@example.com",
            "mcp_url": "http://localhost:51429/mcp",
            "transport": "streamable-http"
        },
        "gn": {
            "provider": "gmail",
            "email": "user@example.org",
            "mcp_url": "http://localhost:51429/mcp",
            "transport": "streamable-http"
        },
        "w": {
            "provider": "whatsapp",
            "bridge_url": "http://127.0.0.1:18741/mcp",
            "bridge_token_file": "/path/to/whatsapp-bridge/store/api_token"
        }
    }

Provider-specific fields:

* **gmail**: ``email`` (required), ``mcp_url``, ``transport``
* **whatsapp**: ``transport`` (``http`` default, or ``stdio``).
  ``http`` takes ``bridge_url``, ``bridge_token``, ``bridge_token_file``;
  ``stdio`` takes ``mcp_cwd`` (required) and ``server_command``.

Common optional field:

* **note**: free-text annotation (e.g. a known noise pattern), set via
  :func:`set_note` — surfaced by ``ts4k sources``.

Usage::

    from ts4k.state import sources

    sources.add("g", provider="gmail", email="alice@gmail.com",
                mcp_url="http://localhost:51429/mcp")
    sources.list_all()        # → {"g": {...}}
    sources.get("g")          # → {"provider": "gmail", ...}
    sources.by_provider("gmail")  # → {"g": {...}, "gn": {...}}
    sources.set_note("oh", "mostly DMARC reports")  # updates only "note"
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_SOURCES_FILE = _CONFIG_DIR / "sources.json"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load() -> dict[str, dict[str, Any]]:
    """Load the source map from disk, or return empty dict."""
    if not _SOURCES_FILE.exists():
        return {}
    try:
        data = json.loads(_SOURCES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    """Persist the source map to disk."""
    from ts4k.state._io import safe_write_json
    safe_write_json(_SOURCES_FILE, data, sort_keys=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get(prefix: str) -> dict[str, Any] | None:
    """Return config for a single source prefix, or ``None``."""
    return _load().get(prefix)


def list_all() -> dict[str, dict[str, Any]]:
    """Return the full source map."""
    return _load()


def by_provider(provider: str) -> dict[str, dict[str, Any]]:
    """Return all sources for a given provider (e.g. ``'gmail'``)."""
    provider = provider.lower()
    return {
        prefix: cfg
        for prefix, cfg in _load().items()
        if cfg.get("provider", "").lower() == provider
    }


def prefixes() -> list[str]:
    """Return all configured source prefixes."""
    return list(_load().keys())


def add(prefix: str, *, provider: str, **kwargs: Any) -> dict[str, Any]:
    """Add or update a source.  Returns the saved config for this prefix."""
    prefix = prefix.strip()
    data = _load()
    entry: dict[str, Any] = {"provider": provider.lower()}
    entry.update(kwargs)
    data[prefix] = entry
    _save(data)
    return entry


def set_note(prefix: str, note: str) -> dict[str, Any] | None:
    """Set or clear the free-text ``note`` field on an existing source.

    Unlike :func:`add`, this only touches the ``note`` key — every other
    field on the source is left alone.  Pass an empty string to clear the
    note.  Returns the updated config, or ``None`` if *prefix* isn't
    configured.
    """
    data = _load()
    if prefix not in data:
        return None
    if note:
        data[prefix]["note"] = note
    else:
        data[prefix].pop("note", None)
    _save(data)
    return data[prefix]


def remove(prefix: str) -> bool:
    """Remove a source.  Returns True if it existed."""
    data = _load()
    if prefix not in data:
        return False
    del data[prefix]
    _save(data)
    return True


def clear() -> None:
    """Remove all sources."""
    if _SOURCES_FILE.exists():
        _SOURCES_FILE.unlink()

"""Keyed watermark tracking — per-key, per-source last-seen timestamps.

Each key (e.g. "life", "peter") gets its own JSON file under
``~/.config/ts4k/watermarks/<key>.json``.  File-per-key avoids contention
between concurrent whatsnew calls with different keys.

File format::

    {
        "g": "2026-03-08T12:00:14Z",
        "o": "2026-03-08T01:19:33Z"
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()


def _wm_dir() -> Path:
    return _CONFIG_DIR / "watermarks"


def _key_file(key: str) -> Path:
    return _wm_dir() / f"{key}.json"


def _load(key: str) -> dict[str, str]:
    f = _key_file(key)
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_all(key: str) -> dict[str, str]:
    """Return all per-source timestamps for *key*."""
    return _load(key)


def get(key: str, source: str) -> str | None:
    """Return the timestamp for *source* within *key*, or None."""
    return _load(key).get(source)


def update(key: str, timestamps: dict[str, str]) -> None:
    """Merge *timestamps* into *key*'s watermark file."""
    from ts4k.state._io import safe_write_json

    data = _load(key)
    data.update(timestamps)
    d = _wm_dir()
    d.mkdir(parents=True, exist_ok=True)
    safe_write_json(_key_file(key), data)


def list_keys() -> list[str]:
    """Return all watermark key names."""
    d = _wm_dir()
    if not d.is_dir():
        return []
    return sorted(f.stem for f in d.glob("*.json"))

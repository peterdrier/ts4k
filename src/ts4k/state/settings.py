"""Global settings — the handful of knobs that are not per-source.

Stores a JSON file at ``~/.config/ts4k/settings.json``.  Everything here is
optional; an absent file means "use the defaults".

File format::

    {
        "timezone": "Europe/Amsterdam"
    }

* **timezone**: IANA name of the single display timezone used to render every
  timestamp.  Absent means "follow the machine".  The ``TS4K_TIMEZONE`` env
  var overrides this file — see :mod:`ts4k.core.tz`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_SETTINGS_FILE = _CONFIG_DIR / "settings.json"


def _load() -> dict[str, Any]:
    """Load the settings file, or return an empty dict."""
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        raw = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def get_timezone() -> str | None:
    """Configured display timezone (IANA name), or None to follow the machine."""
    value = _load().get("timezone")
    return value if isinstance(value, str) and value.strip() else None

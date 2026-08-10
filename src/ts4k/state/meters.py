"""Meter snapshots from HTTP notification sources (#31).

HTTP notification sources (``src/ts4k/adapters/http.py``) can return a
``meters`` list alongside notifications — small summary counters like
``{"label": "Board votes needed", "count": 5, "link": "..."}``. Meters
aren't messages, so they don't belong in the message cache; this stores
the latest snapshot per source prefix so ``ts4k overview`` can surface
them without a live adapter call.

Storing the snapshot is a side effect of polling (see design rule 5 —
"using a command IS the side effect"): every ``whatsnew``/``list`` call
against an HTTP source refreshes it.

File format::

    {"h": [{"label": "Board votes needed", "count": 5, "link": "..."}]}
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_METERS_FILE = _CONFIG_DIR / "meters.json"


def _load() -> dict[str, list[dict[str, Any]]]:
    """Load the meters map from disk, or return empty dict."""
    if not _METERS_FILE.exists():
        return {}
    try:
        data = json.loads(_METERS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def set_meters(prefix: str, meters: list[dict[str, Any]]) -> None:
    """Store the latest meter snapshot for *prefix*. An empty list clears it."""
    from ts4k.state._io import safe_write_json

    data = _load()
    if meters:
        data[prefix] = meters
    else:
        data.pop(prefix, None)
    safe_write_json(_METERS_FILE, data, sort_keys=True)


def get_meters(prefix: str) -> list[dict[str, Any]]:
    """Return the latest meter snapshot for *prefix*, or an empty list."""
    return _load().get(prefix, [])


def all_meters() -> dict[str, list[dict[str, Any]]]:
    """Return the full meters map, keyed by source prefix."""
    return _load()

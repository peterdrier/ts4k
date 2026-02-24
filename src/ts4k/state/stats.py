"""Efficiency stats — track bytes in/out and token savings.

Stores cumulative counters in ``~/.config/ts4k/stats.json``.  Updated
automatically by the CLI pipeline after each command that fetches messages.

File format::

    {
        "total_bytes_in": 125000,
        "total_bytes_out": 18000,
        "total_messages": 340,
        "by_source": {
            "g": {"bytes_in": 100000, "bytes_out": 14000, "messages": 200},
            "w": {"bytes_in": 25000, "bytes_out": 4000, "messages": 140}
        },
        "by_command": {
            "wn": {"calls": 25, "bytes_in": 80000, "bytes_out": 12000},
            "l": {"calls": 10, "bytes_in": 30000, "bytes_out": 4000},
            "g": {"calls": 15, "bytes_in": 15000, "bytes_out": 2000}
        }
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_STATS_FILE = _CONFIG_DIR / "stats.json"

_EMPTY: dict[str, Any] = {
    "total_bytes_in": 0,
    "total_bytes_out": 0,
    "total_messages": 0,
    "by_source": {},
    "by_command": {},
}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load() -> dict[str, Any]:
    """Load stats from disk, or return empty counters."""
    if not _STATS_FILE.exists():
        return _deep_copy_empty()
    try:
        data = json.loads(_STATS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return _deep_copy_empty()


def _deep_copy_empty() -> dict[str, Any]:
    return {
        "total_bytes_in": 0,
        "total_bytes_out": 0,
        "total_messages": 0,
        "by_source": {},
        "by_command": {},
    }


def _save(data: dict[str, Any]) -> None:
    """Persist stats to disk."""
    from ts4k.state._io import safe_write_json
    safe_write_json(_STATS_FILE, data)


# ---------------------------------------------------------------------------
# Public API — recording
# ---------------------------------------------------------------------------


def record(
    *,
    command: str,
    source: str,
    bytes_in: int,
    bytes_out: int,
    messages: int,
) -> None:
    """Record stats for a single operation.

    Parameters
    ----------
    command : str
        CLI command that ran (e.g. ``"wn"``, ``"l"``, ``"g"``).
    source : str
        Source prefix (e.g. ``"g"``, ``"w"``).
    bytes_in : int
        Raw bytes received from upstream adapter.
    bytes_out : int
        Formatted bytes sent to the user/agent.
    messages : int
        Number of messages processed.
    """
    data = _load()

    data["total_bytes_in"] = data.get("total_bytes_in", 0) + bytes_in
    data["total_bytes_out"] = data.get("total_bytes_out", 0) + bytes_out
    data["total_messages"] = data.get("total_messages", 0) + messages

    # Per-source
    src = data.setdefault("by_source", {}).setdefault(source, {
        "bytes_in": 0, "bytes_out": 0, "messages": 0,
    })
    src["bytes_in"] = src.get("bytes_in", 0) + bytes_in
    src["bytes_out"] = src.get("bytes_out", 0) + bytes_out
    src["messages"] = src.get("messages", 0) + messages

    # Per-command
    cmd = data.setdefault("by_command", {}).setdefault(command, {
        "calls": 0, "bytes_in": 0, "bytes_out": 0,
    })
    cmd["calls"] = cmd.get("calls", 0) + 1
    cmd["bytes_in"] = cmd.get("bytes_in", 0) + bytes_in
    cmd["bytes_out"] = cmd.get("bytes_out", 0) + bytes_out

    _save(data)


# ---------------------------------------------------------------------------
# Public API — reading
# ---------------------------------------------------------------------------


def get_all() -> dict[str, Any]:
    """Return the full stats dict."""
    return _load()


def savings_pct() -> float:
    """Return overall byte savings as a percentage (0-100).

    Returns 0.0 if no data has been recorded.
    """
    data = _load()
    bytes_in = data.get("total_bytes_in", 0)
    if bytes_in == 0:
        return 0.0
    bytes_out = data.get("total_bytes_out", 0)
    return round((1 - bytes_out / bytes_in) * 100, 1)


def reset() -> None:
    """Clear all stats."""
    if _STATS_FILE.exists():
        _STATS_FILE.unlink()

"""Filter configuration — opt-in rules for skipping low-value messages.

Stores a JSON file at ``~/.config/ts4k/filters.json``.  Filters are
**off by default** — commands show everything unless ``--filter`` / ``-F``
is passed.  This preserves the triage use case where an LLM agent needs to
see all messages (including junk) to help label, prioritize, or recommend
unsubscribes.

File format::

    {
        "skip_senders": ["noreply@linkedin.com", "notifications@github.com"],
        "skip_domains": ["marketing.example.com"],
        "skip_groups": false,
        "skip_patterns": ["unsubscribe.*click here"]
    }

All fields are optional.  Missing fields use safe defaults (empty / false).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_FILTERS_FILE = _CONFIG_DIR / "filters.json"

# Defaults for every field
_DEFAULTS: dict[str, Any] = {
    "skip_senders": [],
    "skip_domains": [],
    "skip_groups": False,
    "skip_patterns": [],
}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load() -> dict[str, Any]:
    """Load the filter config, merged with defaults."""
    # Deep copy defaults so mutations don't leak back
    data: dict[str, Any] = {
        k: list(v) if isinstance(v, list) else v
        for k, v in _DEFAULTS.items()
    }
    if not _FILTERS_FILE.exists():
        return data
    try:
        raw = json.loads(_FILTERS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update(raw)
    except (json.JSONDecodeError, OSError):
        pass
    return data


def _save(data: dict[str, Any]) -> None:
    """Persist filter config to disk."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _FILTERS_FILE.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Public API — config management
# ---------------------------------------------------------------------------


def get_config() -> dict[str, Any]:
    """Return the full filter config (merged with defaults)."""
    return _load()


def set_config(config: dict[str, Any]) -> dict[str, Any]:
    """Replace the entire filter config.  Returns the saved config."""
    merged = dict(_DEFAULTS)
    merged.update(config)
    _save(merged)
    return merged


def add_sender(sender: str) -> list[str]:
    """Add a sender to the skip list.  Returns the updated list."""
    data = _load()
    sender = sender.strip().lower()
    if sender and sender not in data["skip_senders"]:
        data["skip_senders"].append(sender)
        _save(data)
    return data["skip_senders"]


def remove_sender(sender: str) -> list[str]:
    """Remove a sender from the skip list.  Returns the updated list."""
    data = _load()
    sender = sender.strip().lower()
    if sender in data["skip_senders"]:
        data["skip_senders"].remove(sender)
        _save(data)
    return data["skip_senders"]


def add_domain(domain: str) -> list[str]:
    """Add a domain to the skip list.  Returns the updated list."""
    data = _load()
    domain = domain.strip().lower()
    if domain and domain not in data["skip_domains"]:
        data["skip_domains"].append(domain)
        _save(data)
    return data["skip_domains"]


def remove_domain(domain: str) -> list[str]:
    """Remove a domain from the skip list.  Returns the updated list."""
    data = _load()
    domain = domain.strip().lower()
    if domain in data["skip_domains"]:
        data["skip_domains"].remove(domain)
        _save(data)
    return data["skip_domains"]


def add_pattern(pattern: str) -> list[str]:
    """Add a regex pattern to skip.  Returns the updated list."""
    data = _load()
    pattern = pattern.strip()
    if pattern and pattern not in data["skip_patterns"]:
        data["skip_patterns"].append(pattern)
        _save(data)
    return data["skip_patterns"]


def remove_pattern(pattern: str) -> list[str]:
    """Remove a pattern from the skip list.  Returns the updated list."""
    data = _load()
    pattern = pattern.strip()
    if pattern in data["skip_patterns"]:
        data["skip_patterns"].remove(pattern)
        _save(data)
    return data["skip_patterns"]


def set_skip_groups(skip: bool) -> bool:
    """Set whether to skip group chat messages.  Returns the new value."""
    data = _load()
    data["skip_groups"] = bool(skip)
    _save(data)
    return data["skip_groups"]


def reset() -> dict[str, Any]:
    """Reset filter config to defaults.  Returns the default config."""
    if _FILTERS_FILE.exists():
        _FILTERS_FILE.unlink()
    return dict(_DEFAULTS)

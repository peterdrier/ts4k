"""Cross-platform contact identity map.

Stores a JSON file at ``~/.config/ts4k/contacts.json`` that links
platform-specific identifiers to a single human-readable alias.

File format::

    {
        "sarah": ["g:sarah@gmail.com", "w:15551234567@s.whatsapp.net"],
        "mom":   ["g:mom@example.com", "w:15559876543@s.whatsapp.net"]
    }

Identifiers use the same ``<source>:<id>`` prefix convention as message IDs.
The alias is a short, human-friendly label — typically a first name or
nickname.  The *LLM agent* decides when to link identifiers; ts4k just
stores and queries.

Usage::

    from ts4k.state import contacts

    contacts.link("sarah", "g:sarah@gmail.com", "w:15551234567@s.whatsapp.net")
    contacts.query("sarah")        # → ["g:sarah@gmail.com", "w:15551234567@s.whatsapp.net"]
    contacts.resolve("g:sarah@gmail.com")  # → "sarah"
    contacts.find("sar")           # → {"sarah": ["g:...", "w:..."]}
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_CONTACTS_FILE = _CONFIG_DIR / "contacts.json"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load() -> dict[str, list[str]]:
    """Load the contact map from disk, or return empty dict."""
    if not _CONTACTS_FILE.exists():
        return {}
    try:
        data = json.loads(_CONTACTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, list[str]]) -> None:
    """Persist the contact map to disk."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONTACTS_FILE.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def link(alias: str, *identifiers: str) -> list[str]:
    """Add one or more identifiers to *alias*.

    Creates the alias if it doesn't exist.  Deduplicates.
    Returns the full identifier list for the alias.
    """
    alias = alias.lower().strip()
    data = _load()
    existing = data.get(alias, [])
    for ident in identifiers:
        ident = ident.strip()
        if ident and ident not in existing:
            existing.append(ident)
    data[alias] = existing
    _save(data)
    return existing


def unlink(alias: str, *identifiers: str) -> list[str] | None:
    """Remove identifiers from *alias*.

    If no identifiers given, removes the entire alias.
    Returns remaining identifiers, or ``None`` if alias was deleted.
    """
    alias = alias.lower().strip()
    data = _load()
    if alias not in data:
        return None

    if not identifiers:
        del data[alias]
        _save(data)
        return None

    existing = data[alias]
    for ident in identifiers:
        ident = ident.strip()
        if ident in existing:
            existing.remove(ident)

    if not existing:
        del data[alias]
        _save(data)
        return None

    data[alias] = existing
    _save(data)
    return existing


def query(alias: str) -> list[str]:
    """Return all identifiers for *alias*, or empty list."""
    return _load().get(alias.lower().strip(), [])


def resolve(identifier: str) -> str | None:
    """Reverse lookup: find the alias that contains *identifier*.

    Returns the alias name, or ``None`` if not linked.
    """
    identifier = identifier.strip()
    for alias, idents in _load().items():
        if identifier in idents:
            return alias
    return None


def find(term: str) -> dict[str, list[str]]:
    """Search contacts by alias or identifier substring.

    Returns matching entries as ``{alias: [identifiers]}``.
    """
    term = term.lower().strip()
    results: dict[str, list[str]] = {}
    for alias, idents in _load().items():
        if term in alias:
            results[alias] = idents
            continue
        for ident in idents:
            if term in ident.lower():
                results[alias] = idents
                break
    return results


def list_all() -> dict[str, list[str]]:
    """Return the full contact map."""
    return _load()


def clear() -> None:
    """Delete all contacts."""
    if _CONTACTS_FILE.exists():
        _CONTACTS_FILE.unlink()

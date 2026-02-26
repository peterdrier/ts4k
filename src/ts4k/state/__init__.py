"""ts4k state — centralized config directory resolution.

Resolution order (first match wins):
1. ``TS4K_CONFIG_DIR`` env var  →  reason: ``"env"``
2. ``.ts4k/`` in cwd            →  reason: ``"local"``
3. ``~/.config/ts4k/``          →  reason: ``"default"``

Call :func:`get_config_dir` to lazily resolve, or :func:`set_config_dir` to
override and propagate to all state modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConfigDir:
    """Resolved config directory with provenance."""

    path: Path
    reason: str  # "env", "local", "default", "local-flag", "context", "test"


_current: ConfigDir | None = None


def resolve_config_dir() -> ConfigDir:
    """Resolve the config directory using the three-tier chain.

    Does NOT cache — use :func:`get_config_dir` for the cached version.
    """
    env = os.environ.get("TS4K_CONFIG_DIR")
    if env:
        return ConfigDir(path=Path(env).expanduser(), reason="env")

    local = Path.cwd() / ".ts4k"
    if local.is_dir():
        return ConfigDir(path=local, reason="local")

    return ConfigDir(path=Path.home() / ".config" / "ts4k", reason="default")


def get_config_dir() -> ConfigDir:
    """Return the cached config directory, resolving on first call."""
    global _current
    if _current is None:
        _current = resolve_config_dir()
    return _current


def set_config_dir(path: Path, reason: str = "override") -> ConfigDir:
    """Override the config directory and propagate to all state modules.

    Returns the new :class:`ConfigDir`.
    """
    global _current
    _current = ConfigDir(path=path, reason=reason)
    _apply_to_modules(path)
    return _current


def _apply_to_modules(config_dir: Path) -> None:
    """Patch ``_CONFIG_DIR`` and derived paths on all state modules."""
    from ts4k.state import batch, cache, contacts, filters, sources, stats, watermarks

    # watermarks
    watermarks._CONFIG_DIR = config_dir
    watermarks._WM_FILE = config_dir / "watermarks.json"

    # sources
    sources._CONFIG_DIR = config_dir
    sources._SOURCES_FILE = config_dir / "sources.json"

    # filters
    filters._CONFIG_DIR = config_dir
    filters._FILTERS_FILE = config_dir / "filters.json"

    # stats
    stats._CONFIG_DIR = config_dir
    stats._STATS_FILE = config_dir / "stats.json"

    # contacts
    contacts._CONFIG_DIR = config_dir
    contacts._CONTACTS_FILE = config_dir / "contacts.json"

    # batch
    batch._CONFIG_DIR = config_dir
    batch._BATCH_FILE = config_dir / "batch.json"

    # cache
    cache._CONFIG_DIR = config_dir
    cache_dir = config_dir / "cache"
    cache._CACHE_DIR = cache_dir
    cache._INDEX_FILE = cache_dir / "index.json"


def reset() -> None:
    """Clear cached state. Intended for tests."""
    global _current
    _current = None

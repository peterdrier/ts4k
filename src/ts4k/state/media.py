"""Media download store — local files fetched via ``ts4k get --media``.

Stores files under ``~/.config/ts4k/media/``. This is a new class of side
effect for ts4k (writing user-visible files outside its own config dir has
never happened before this), so downloads are confined to this one
directory rather than landing in the current working directory.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_MEDIA_DIR = _CONFIG_DIR / "media"


def media_dir() -> Path:
    """Return the media directory, creating it on demand."""
    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    return _MEDIA_DIR


def save_media(src_path: str, msg_id: str) -> Path:
    """Copy a downloaded file from *src_path* into the ts4k media store.

    Named ``<safe-id>-<original filename>`` so files from different
    messages never collide. Returns the absolute destination path.
    """
    src = Path(src_path)
    safe_id = msg_id.replace(":", "_").replace("/", "_")
    dest = media_dir() / f"{safe_id}-{src.name}"
    shutil.copy2(src, dest)
    return dest.resolve()

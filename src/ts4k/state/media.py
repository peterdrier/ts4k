"""Media download store — local files fetched via ``ts4k get --media``.

Stores files under ``~/.config/ts4k/media/``. This is a new class of side
effect for ts4k (writing user-visible files outside its own config dir has
never happened before this), so downloads are confined to this one
directory rather than landing in the current working directory.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_MEDIA_DIR = _CONFIG_DIR / "media"

# Anything outside this set (including both path separators, "..", and
# drive-letter colons) is mapped to "_" so a crafted message ID can't
# steer the destination path outside the media directory.
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def media_dir() -> Path:
    """Return the media directory, creating it on demand.

    Downloaded media is private, so the directory (and each file copied
    into it, see ``save_media``) is restricted to the owner. ``mkdir``'s
    ``mode`` is subject to umask and has no effect if the directory
    already exists, so permissions are fixed up explicitly afterward.
    """
    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(_MEDIA_DIR, 0o700)
    return _MEDIA_DIR


def save_media(src_path: str, msg_id: str) -> Path:
    """Copy a downloaded file from *src_path* into the ts4k media store.

    Named ``<safe-id>-<original filename>`` so files from different
    messages never collide. Returns the absolute destination path.
    """
    src = Path(src_path)
    safe_id = _UNSAFE_ID_CHARS.sub("_", msg_id)
    media_root = media_dir()
    dest = (media_root / f"{safe_id}-{src.name}").resolve()
    if not dest.is_relative_to(media_root.resolve()):
        raise ValueError(f"unsafe media id: {msg_id!r}")
    shutil.copy2(src, dest)
    if os.name != "nt":
        os.chmod(dest, 0o600)
    return dest

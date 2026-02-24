"""Atomic JSON write helper shared by all state modules.

Uses ``tempfile.mkstemp`` + ``os.replace`` to ensure writes are atomic on
both POSIX and Windows NTFS.  A crash mid-write leaves the previous file
intact rather than a truncated (corrupt) one.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def safe_write_json(
    path: Path,
    data: Any,
    *,
    indent: int | None = 2,
    sort_keys: bool = False,
) -> None:
    """Atomically write *data* as JSON to *path*.

    - Creates parent directories as needed.
    - Writes to a temp file in the same directory, then ``os.replace`` for
      atomic swap (same-filesystem guarantee).
    - Cleans up the temp file on any failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, sort_keys=sort_keys)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

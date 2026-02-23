"""Batch job state — track preload progress with crash-safe checkpointing.

Stores a JSON file at ``~/.config/ts4k/batch.json`` with one entry per
preload job.  Each page-fetch updates the checkpoint so a crashed or
interrupted preload can be resumed from the last completed page.

File format::

    {
        "jobs": {
            "job-1708600000": {
                "source": "g",
                "query": "from:alice@example.com",
                "status": "in_progress",
                "pages_fetched": 5,
                "messages_cached": 247,
                "next_page_token": "token_xyz",
                "started_at": "2026-02-22T10:00:00Z",
                "updated_at": "2026-02-22T10:05:00Z",
                "error": null
            }
        }
    }
"""

from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()
_BATCH_FILE = _CONFIG_DIR / "batch.json"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load() -> dict[str, Any]:
    """Load batch state from disk, or return empty structure."""
    if not _BATCH_FILE.exists():
        return {"jobs": {}}
    try:
        data = json.loads(_BATCH_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "jobs" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"jobs": {}}


def _save(data: dict[str, Any]) -> None:
    """Persist batch state to disk."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _BATCH_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_job(source: str, query: str) -> str:
    """Create a new preload job and return its ID.

    The job starts with status ``"in_progress"`` and zero pages fetched.
    """
    job_id = f"job-{int(time.time())}"
    now = _now_iso()
    data = _load()
    data["jobs"][job_id] = {
        "source": source,
        "query": query,
        "status": "in_progress",
        "pages_fetched": 0,
        "messages_cached": 0,
        "next_page_token": None,
        "started_at": now,
        "updated_at": now,
        "error": None,
    }
    _save(data)
    return job_id


def update_job(job_id: str, **fields: Any) -> None:
    """Update one or more fields on an existing job and checkpoint to disk.

    Always sets ``updated_at`` to the current time.
    """
    data = _load()
    job = data["jobs"].get(job_id)
    if job is None:
        raise KeyError(f"Unknown job: {job_id}")
    job.update(fields)
    job["updated_at"] = _now_iso()
    _save(data)


def get_job(job_id: str) -> dict | None:
    """Return the job dict for *job_id*, or ``None`` if not found."""
    return _load()["jobs"].get(job_id)


def list_jobs() -> dict[str, dict]:
    """Return all jobs as ``{job_id: job_dict}``."""
    return _load()["jobs"]


def is_running(job_id: str) -> bool:
    """Check whether the background process for *job_id* is still alive.

    Uses ``os.kill(pid, 0)`` (signal 0 = existence check, no signal sent).
    Returns ``False`` if no PID is stored or the process is gone.
    """
    job = get_job(job_id)
    if job is None:
        return False
    pid = job.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def delete_job(job_id: str) -> bool:
    """Delete a job.  Returns ``True`` if it existed."""
    data = _load()
    if job_id in data["jobs"]:
        del data["jobs"][job_id]
        _save(data)
        return True
    return False

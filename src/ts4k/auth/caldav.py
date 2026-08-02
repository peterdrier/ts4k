"""CalDAV credential storage — Apple app-specific passwords, no OAuth.

Credentials live at ``~/.config/ts4k/caldav/<email>/credentials.json``
(0600).  Generate an app-specific password at https://account.apple.com
(Sign-In and Security → App-Specific Passwords; requires 2FA).

Apple issues one app-specific password per Apple ID, not per service, so
the CardDAV adapter reads the same credential file — only the base URL
differs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ICLOUD_CALDAV_URL = "https://caldav.icloud.com"
ICLOUD_CARDDAV_URL = "https://contacts.icloud.com"


def is_icloud_carddav_url(url: str) -> bool:
    """True when *url* points at the iCloud CardDAV endpoint.

    Compares scheme + lowercased host only, ignoring path — a shard
    redirect's trailing slash or a differently-cased host must still
    count as iCloud, or credential reuse and shard trust silently break
    for anything that isn't byte-identical to ``ICLOUD_CARDDAV_URL``.
    """
    canonical = urlsplit(ICLOUD_CARDDAV_URL)
    candidate = urlsplit(url)
    try:
        candidate_port = candidate.port
    except ValueError:
        return False
    # Compare hostname + effective port so an explicit default port
    # (https://contacts.icloud.com:443) still counts as iCloud
    return (
        candidate.scheme.lower() == canonical.scheme.lower()
        and (candidate.hostname or "") == (canonical.hostname or "")
        and (candidate_port or 443) == (canonical.port or 443)
    )


def _default_config_dir() -> Path:
    """Resolve auth config dir: env var → global default (mirrors auth/google.py)."""
    env = os.environ.get("TS4K_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "ts4k"


def credentials_path(email: str, config_dir: Path | None = None) -> Path:
    base = Path(config_dir) if config_dir else _default_config_dir()
    return base / "caldav" / email / "credentials.json"


def save_credentials(
    email: str,
    *,
    username: str,
    app_password: str,
    server_url: str,
    config_dir: Path | None = None,
) -> Path:
    path = credentials_path(email, config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"username": username, "app_password": app_password, "server_url": server_url},
        indent=2,
    )
    # Create 0600 up front (and tighten an existing file) so the password is
    # never on disk under looser permissions, not even briefly.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(payload)
    return path


def load_credentials(email: str, config_dir: Path | None = None) -> dict | None:
    path = credentials_path(email, config_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text())

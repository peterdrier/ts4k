"""CalDAV credential storage — Apple app-specific passwords, no OAuth.

Credentials live at ``~/.config/ts4k/caldav/<email>/credentials.json``
(0600).  Generate an app-specific password at https://account.apple.com
(Sign-In and Security → App-Specific Passwords; requires 2FA).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ICLOUD_CALDAV_URL = "https://caldav.icloud.com"


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
    path.write_text(json.dumps(
        {"username": username, "app_password": app_password, "server_url": server_url},
        indent=2,
    ))
    path.chmod(0o600)
    return path


def load_credentials(email: str, config_dir: Path | None = None) -> dict | None:
    path = credentials_path(email, config_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text())

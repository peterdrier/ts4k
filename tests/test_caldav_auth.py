"""Tests for CalDAV credential storage."""

from __future__ import annotations

import os
from pathlib import Path

from ts4k.auth.caldav import (
    ICLOUD_CALDAV_URL,
    credentials_path,
    load_credentials,
    save_credentials,
)


def test_path_layout(tmp_path: Path):
    p = credentials_path("a@icloud.com", config_dir=tmp_path)
    assert p == tmp_path / "caldav" / "a@icloud.com" / "credentials.json"


def test_save_and_load_roundtrip(tmp_path: Path):
    save_credentials(
        "a@icloud.com",
        username="a@icloud.com",
        app_password="abcd-efgh-ijkl-mnop",
        server_url=ICLOUD_CALDAV_URL,
        config_dir=tmp_path,
    )
    creds = load_credentials("a@icloud.com", config_dir=tmp_path)
    assert creds == {
        "username": "a@icloud.com",
        "app_password": "abcd-efgh-ijkl-mnop",
        "server_url": ICLOUD_CALDAV_URL,
    }


def test_file_permissions_0600(tmp_path: Path):
    path = save_credentials(
        "a@icloud.com",
        username="a@icloud.com",
        app_password="x",
        server_url=ICLOUD_CALDAV_URL,
        config_dir=tmp_path,
    )
    assert (path.stat().st_mode & 0o777) == 0o600


def test_permissions_come_from_creation_not_followup_chmod(tmp_path: Path, monkeypatch):
    """No window where the password is on disk world-readable.

    Simulates the absence of a fix-up chmod: the mode must already be 0600
    when the bytes are written, even under a permissive umask.
    """
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)
    old_umask = os.umask(0)
    try:
        path = save_credentials(
            "a@icloud.com",
            username="a@icloud.com",
            app_password="x",
            server_url=ICLOUD_CALDAV_URL,
            config_dir=tmp_path,
        )
    finally:
        os.umask(old_umask)
    assert (path.stat().st_mode & 0o777) == 0o600


def test_rewrite_tightens_preexisting_loose_permissions(tmp_path: Path):
    path = credentials_path("a@icloud.com", config_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    path.chmod(0o644)

    save_credentials(
        "a@icloud.com",
        username="a@icloud.com",
        app_password="x",
        server_url=ICLOUD_CALDAV_URL,
        config_dir=tmp_path,
    )
    assert (path.stat().st_mode & 0o777) == 0o600


def test_load_missing_returns_none(tmp_path: Path):
    assert load_credentials("nobody@icloud.com", config_dir=tmp_path) is None

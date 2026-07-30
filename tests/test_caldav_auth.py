"""Tests for CalDAV credential storage."""

from __future__ import annotations

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


def test_load_missing_returns_none(tmp_path: Path):
    assert load_credentials("nobody@icloud.com", config_dir=tmp_path) is None

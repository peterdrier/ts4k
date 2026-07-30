"""Tests for CalDAV provider registration in commands.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ts4k import commands
from ts4k.adapters.caldav_cal import CaldavAdapter

CALDAV_CFG = {
    "provider": "caldav",
    "email": "a@icloud.com",
    "server_url": "https://caldav.icloud.com",
    "calendar_id": "https://caldav.icloud.com/123/calendars/home/",
    "calendar_name": "Home",
    "timezone": "UTC",
    "level": "modify",
}


class TestMakeAdapter:
    def test_builds_caldav_adapter(self):
        a = commands._make_adapter("cc", dict(CALDAV_CFG))
        assert isinstance(a, CaldavAdapter)
        assert a.source_prefix == "cc"

    def test_missing_email_returns_none(self):
        cfg = dict(CALDAV_CFG)
        del cfg["email"]
        assert commands._make_adapter("cc", cfg) is None

    def test_missing_calendar_id_returns_none(self):
        cfg = dict(CALDAV_CFG)
        del cfg["calendar_id"]
        assert commands._make_adapter("cc", cfg) is None


class TestResolvePrefixes:
    def test_apple_alias_resolves_to_caldav_sources(self, monkeypatch):
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"cc": dict(CALDAV_CFG)}
        )
        assert commands._resolve_prefixes("apple") == ["cc"]
        assert commands._resolve_prefixes("icloud") == ["cc"]
        assert commands._resolve_prefixes("caldav") == ["cc"]


class TestCalGates:
    async def test_cal_create_accepts_caldav_source(self, monkeypatch):
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"cc": dict(CALDAV_CFG)}
        )
        fake = MagicMock()
        fake.__aenter__ = AsyncMock(return_value=fake)
        fake.__aexit__ = AsyncMock(return_value=None)
        fake.create_event = AsyncMock(return_value={"title": "X", "id": "cc:1"})
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: fake)
        out = await commands.cal_create(
            "cc", "X", "2026-07-30T10:00:00", "2026-07-30T11:00:00",
            None, None, None,
        )
        assert out == "Created: X (cc:1)"


class TestTokenHealth:
    def test_caldav_with_credentials_is_ok(self, tmp_path: Path, monkeypatch):
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL, save_credentials

        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        save_credentials("a@icloud.com", username="a@icloud.com",
                         app_password="x", server_url=ICLOUD_CALDAV_URL)
        th = commands.check_token_health("cc", dict(CALDAV_CFG))
        assert th.status == "ok"

    def test_caldav_without_credentials_is_na(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        th = commands.check_token_health("cc", dict(CALDAV_CFG))
        assert th.status == "na"

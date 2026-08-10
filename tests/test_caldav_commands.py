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


class TestCalSourceAliases:
    """`cal today --source apple` must reach caldav sources, not silently return []."""

    async def test_alias_fetches_from_caldav_source(self, monkeypatch):
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"cc": dict(CALDAV_CFG)}
        )
        event = {"id": "cc:1", "title": "Standup", "start": "2026-07-30T09:00:00"}
        adapter = MagicMock()
        adapter.__aenter__ = AsyncMock(return_value=adapter)
        adapter.__aexit__ = AsyncMock(return_value=None)
        adapter.list_events = AsyncMock(return_value=[event])
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: adapter)

        for alias in ("apple", "icloud", "caldav", "cc"):
            events = await commands._cal_fetch_events(
                alias, "2026-07-30T00:00:00", "2026-07-31T00:00:00"
            )
            assert events == [event], f"alias {alias!r} returned {events}"

    async def test_exact_case_prefix_still_matches(self, monkeypatch):
        """`src add` preserves prefix case — an exact key must win over lowercasing."""
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"Work": dict(CALDAV_CFG)}
        )
        event = {"id": "Work:1", "title": "Standup", "start": "2026-07-30T09:00:00"}
        adapter = MagicMock()
        adapter.__aenter__ = AsyncMock(return_value=adapter)
        adapter.__aexit__ = AsyncMock(return_value=None)
        adapter.list_events = AsyncMock(return_value=[event])
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: adapter)

        assert commands._resolve_prefixes("Work") == ["Work"]
        events = await commands._cal_fetch_events(
            "Work", "2026-07-30T00:00:00", "2026-07-31T00:00:00"
        )
        assert events == [event]

    def test_unknown_source_still_matches_nothing(self, monkeypatch):
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"cc": dict(CALDAV_CFG)}
        )
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: MagicMock())

        async def _run():
            return await commands._cal_fetch_events(
                "nosuch", "2026-07-30T00:00:00", "2026-07-31T00:00:00"
            )

        import asyncio

        assert asyncio.run(_run()) == []


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


class TestCalFetchEventsIsolation:
    async def test_one_source_failing_does_not_abort_others(self, monkeypatch):
        # A revoked app-specific password (or any adapter connect failure)
        # should not take down `cal today/week` for the other sources.
        broken_cfg = dict(CALDAV_CFG)
        ok_cfg = dict(CALDAV_CFG)

        monkeypatch.setattr(
            "ts4k.state.sources.list_all",
            lambda: {"broken": broken_cfg, "ok": ok_cfg},
        )

        broken_adapter = MagicMock()
        broken_adapter.__aenter__ = AsyncMock(side_effect=RuntimeError("bad app password"))
        broken_adapter.__aexit__ = AsyncMock(return_value=None)

        ok_event = {"id": "ok:1", "title": "Standup", "start": "2026-07-30T09:00:00"}
        ok_adapter = MagicMock()
        ok_adapter.__aenter__ = AsyncMock(return_value=ok_adapter)
        ok_adapter.__aexit__ = AsyncMock(return_value=None)
        ok_adapter.list_events = AsyncMock(return_value=[ok_event])

        def fake_make_adapter(prefix, cfg):
            return broken_adapter if prefix == "broken" else ok_adapter

        monkeypatch.setattr(commands, "_make_adapter", fake_make_adapter)

        events = await commands._cal_fetch_events(
            None, "2026-07-30T00:00:00", "2026-07-31T00:00:00"
        )
        assert events == [ok_event]

    async def test_explicit_source_failure_raises(self, monkeypatch):
        # When the caller explicitly asked for one source (-s cc) and it
        # fails, silently returning no events is misleading — surface the
        # failure instead of swallowing it.
        broken_cfg = dict(CALDAV_CFG)

        monkeypatch.setattr(
            "ts4k.state.sources.list_all",
            lambda: {"cc": broken_cfg},
        )

        broken_adapter = MagicMock()
        broken_adapter.__aenter__ = AsyncMock(side_effect=RuntimeError("bad app password"))
        broken_adapter.__aexit__ = AsyncMock(return_value=None)

        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: broken_adapter)

        with pytest.raises(RuntimeError, match="cc"):
            await commands._cal_fetch_events(
                "cc", "2026-07-30T00:00:00", "2026-07-31T00:00:00"
            )


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

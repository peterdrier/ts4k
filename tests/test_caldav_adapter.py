"""Tests for the CalDAV calendar adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ts4k.adapters.caldav_cal import CaldavAdapter, CaldavAdapterConfig
from ts4k.auth.caldav import ICLOUD_CALDAV_URL, save_credentials
from ts4k.core.levels import AccessLevel


@pytest.fixture
def caldav_config(tmp_path: Path) -> CaldavAdapterConfig:
    save_credentials(
        "test@icloud.com",
        username="test@icloud.com",
        app_password="abcd-efgh",
        server_url=ICLOUD_CALDAV_URL,
        config_dir=tmp_path,
    )
    return CaldavAdapterConfig(
        email="test@icloud.com",
        server_url=ICLOUD_CALDAV_URL,
        calendar_id="https://caldav.icloud.com/123/calendars/home/",
        calendar_name="Home",
        timezone="Europe/Amsterdam",
        config_dir=tmp_path,
        level="readonly",
    )


@pytest.fixture
def adapter(caldav_config: CaldavAdapterConfig) -> CaldavAdapter:
    a = CaldavAdapter(caldav_config, prefix="cc")
    # Bypass network: tests install mocks where connect() would put real objects
    a._principal = MagicMock()
    a._calendar = MagicMock()
    return a


class TestConstruction:
    def test_prefix(self, adapter: CaldavAdapter):
        assert adapter.source_prefix == "cc"

    def test_access_level(self, adapter: CaldavAdapter):
        assert adapter.access_level == AccessLevel.READONLY


class TestConnect:
    async def test_connect_without_credentials_raises_actionable(self, tmp_path: Path):
        config = CaldavAdapterConfig(
            email="nobody@icloud.com",
            server_url=ICLOUD_CALDAV_URL,
            calendar_id="x",
            config_dir=tmp_path,
        )
        a = CaldavAdapter(config, prefix="cc")
        with pytest.raises(RuntimeError, match="app-specific password"):
            await a.connect()


class TestMessagingStubs:
    """Messaging methods return empty results (not raise) for --source all safety."""

    async def test_whatsnew_returns_empty(self, adapter: CaldavAdapter):
        assert await adapter.whatsnew(since="2026-01-01") == []

    async def test_list_messages_returns_empty(self, adapter: CaldavAdapter):
        assert await adapter.list_messages() == []

    async def test_read_message_raises(self, adapter: CaldavAdapter):
        with pytest.raises(NotImplementedError):
            await adapter.read_message("cc:123")

    async def test_read_thread_raises(self, adapter: CaldavAdapter):
        with pytest.raises(NotImplementedError):
            await adapter.read_thread("cc:t123")


class TestStripPrefix:
    def test_strips_own_prefix(self, adapter: CaldavAdapter):
        assert adapter._strip_prefix("cc:abc123") == "abc123"

    def test_leaves_bare_id(self, adapter: CaldavAdapter):
        assert adapter._strip_prefix("abc123") == "abc123"

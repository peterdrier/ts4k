"""Tests for the Google Calendar adapter."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from ts4k.adapters.gcal import GcalAdapter, GcalAdapterConfig
from ts4k.core.levels import AccessLevel


@pytest.fixture
def gcal_config(tmp_path: Path) -> GcalAdapterConfig:
    return GcalAdapterConfig(
        email="test@gmail.com",
        calendar_id="primary",
        calendar_name="Test Calendar",
        timezone="Europe/Amsterdam",
        config_dir=tmp_path,
        level="readonly",
    )


@pytest.fixture
def adapter(gcal_config: GcalAdapterConfig) -> GcalAdapter:
    return GcalAdapter(gcal_config, prefix="gc")


class TestConstruction:
    def test_prefix(self, adapter: GcalAdapter):
        assert adapter.source_prefix == "gc"

    def test_access_level(self, adapter: GcalAdapter):
        assert adapter.access_level == AccessLevel.READONLY


class TestMessagingStubs:
    """Messaging methods return empty results (not raise) for --source all safety."""

    async def test_whatsnew_returns_empty(self, adapter: GcalAdapter):
        result = await adapter.whatsnew(since="2026-01-01")
        assert result == []

    async def test_list_messages_returns_empty(self, adapter: GcalAdapter):
        result = await adapter.list_messages()
        assert result == []

    async def test_read_message_raises(self, adapter: GcalAdapter):
        with pytest.raises(NotImplementedError):
            await adapter.read_message("gc:123")

    async def test_read_thread_raises(self, adapter: GcalAdapter):
        with pytest.raises(NotImplementedError):
            await adapter.read_thread("gc:t123")

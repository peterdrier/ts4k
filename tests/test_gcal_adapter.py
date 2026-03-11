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


class TestListEvents:
    """Test list_events: API call, pagination, normalization."""

    async def test_basic_timed_event(self, adapter: GcalAdapter):
        """Timed event is normalized correctly."""
        api_event = {
            "id": "evt1",
            "summary": "Standup",
            "start": {"dateTime": "2026-03-11T09:00:00+01:00"},
            "end": {"dateTime": "2026-03-11T09:30:00+01:00"},
            "status": "confirmed",
            "organizer": {"email": "sarah@work.com"},
            "attendees": [
                {"email": "sarah@work.com", "responseStatus": "accepted"},
                {"email": "test@gmail.com", "responseStatus": "accepted", "self": True},
                {"email": "mike@work.com", "responseStatus": "tentative"},
            ],
        }
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [api_event],
        }
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00+01:00",
            time_max="2026-03-12T00:00:00+01:00",
        )

        assert len(result) == 1
        evt = result[0]
        assert evt["id"] == "gc:evt1"
        assert evt["title"] == "Standup"
        assert evt["all_day"] is False
        assert evt["duration_minutes"] == 30
        assert evt["organizer"] == "sarah@work.com"
        assert evt["attendees_summary"] == "3 people"
        assert evt["your_status"] == "accepted"
        assert evt["recurring_event_id"] is None

    async def test_all_day_event(self, adapter: GcalAdapter):
        """All-day event uses date keys, not dateTime."""
        api_event = {
            "id": "evt2",
            "summary": "Vacation",
            "start": {"date": "2026-03-17"},
            "end": {"date": "2026-03-22"},  # exclusive: Mon-Fri = Sat end
            "status": "confirmed",
        }
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [api_event],
        }
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-17T00:00:00+01:00",
            time_max="2026-03-23T00:00:00+01:00",
        )

        evt = result[0]
        assert evt["all_day"] is True
        assert evt["start"] == "2026-03-17"
        assert evt["end"] == "2026-03-22"  # raw API value (exclusive)

    async def test_recurring_event_has_recurring_id(self, adapter: GcalAdapter):
        """Expanded recurring instance includes recurringEventId."""
        api_event = {
            "id": "evt1_20260311",
            "recurringEventId": "evt1",
            "summary": "Standup",
            "start": {"dateTime": "2026-03-11T09:00:00+01:00"},
            "end": {"dateTime": "2026-03-11T09:30:00+01:00"},
            "status": "confirmed",
        }
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [api_event],
        }
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00+01:00",
            time_max="2026-03-12T00:00:00+01:00",
        )

        assert result[0]["recurring_event_id"] == "gc:evt1"

    async def test_pagination(self, adapter: GcalAdapter):
        """Follows nextPageToken to fetch all events."""
        page1 = {
            "items": [{"id": "e1", "summary": "A", "start": {"dateTime": "2026-03-11T09:00:00Z"}, "end": {"dateTime": "2026-03-11T10:00:00Z"}, "status": "confirmed"}],
            "nextPageToken": "token2",
        }
        page2 = {
            "items": [{"id": "e2", "summary": "B", "start": {"dateTime": "2026-03-11T11:00:00Z"}, "end": {"dateTime": "2026-03-11T12:00:00Z"}, "status": "confirmed"}],
        }
        mock_service = MagicMock()
        mock_list = mock_service.events.return_value.list
        mock_list.return_value.execute.side_effect = [page1, page2]
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00Z",
            time_max="2026-03-12T00:00:00Z",
        )

        assert len(result) == 2
        assert result[0]["title"] == "A"
        assert result[1]["title"] == "B"

    async def test_declined_event_included(self, adapter: GcalAdapter):
        """Declined events are included with your_status='declined'."""
        api_event = {
            "id": "evt3",
            "summary": "Skipped Meeting",
            "start": {"dateTime": "2026-03-11T14:00:00+01:00"},
            "end": {"dateTime": "2026-03-11T15:00:00+01:00"},
            "status": "confirmed",
            "attendees": [
                {"email": "test@gmail.com", "responseStatus": "declined", "self": True},
            ],
        }
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [api_event],
        }
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00+01:00",
            time_max="2026-03-12T00:00:00+01:00",
        )

        assert result[0]["your_status"] == "declined"

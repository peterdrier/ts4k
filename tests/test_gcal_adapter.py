"""Tests for the Google Calendar adapter."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestConnectScopes:
    """connect() must request the per-email scope union, not just its own scopes."""

    async def test_connect_requests_union_scopes(self, adapter: GcalAdapter):
        srcs = {
            "g": {"provider": "gmail", "email": "test@gmail.com", "level": "modify"},
            "gc": {"provider": "gcal", "email": "test@gmail.com"},
        }
        with patch("ts4k.state.sources.list_all", return_value=srcs), \
             patch("ts4k.auth.google.build_calendar_service") as mock_build:
            await adapter.connect()

        scopes = mock_build.call_args.kwargs["scopes"]
        assert "https://www.googleapis.com/auth/calendar.readonly" in scopes
        assert "https://www.googleapis.com/auth/gmail.modify" in scopes


class TestMessagingStubs:
    """Messaging methods return empty results (not raise) for --source all safety."""

    async def test_whatsnew_returns_empty(self, adapter: GcalAdapter):
        result = await adapter.whatsnew(since="2026-01-01")
        assert result == []

    async def test_whatsnew_accepts_count(self, adapter: GcalAdapter):
        """The command layer passes count= to every non-Gmail source."""
        result = await adapter.whatsnew(since="2026-01-01", count=200)
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


class TestReadEvent:
    async def test_full_detail(self, adapter: GcalAdapter):
        api_event = {
            "id": "evt1",
            "summary": "Budget Review",
            "start": {"dateTime": "2026-03-11T11:00:00+01:00"},
            "end": {"dateTime": "2026-03-11T12:00:00+01:00"},
            "status": "confirmed",
            "organizer": {"email": "sarah@work.com", "displayName": "Sarah Chen"},
            "description": "Review Q1 numbers.",
            "location": "Room 4A",
            "attendees": [
                {"email": "sarah@work.com", "displayName": "Sarah Chen", "responseStatus": "accepted"},
                {"email": "test@gmail.com", "responseStatus": "accepted", "self": True},
            ],
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU"],
            "created": "2026-01-15T10:00:00.000Z",
            "updated": "2026-03-10T14:30:00.000Z",
            "conferenceData": {
                "entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/xyz"}],
            },
        }
        mock_service = MagicMock()
        mock_service.events.return_value.get.return_value.execute.return_value = api_event
        adapter._service = mock_service

        result = await adapter.read_event("gc:evt1")

        assert result["description"] == "Review Q1 numbers."
        assert result["meeting_link"] == "https://meet.google.com/xyz"
        assert len(result["attendees"]) == 2
        assert result["attendees"][0]["name"] == "Sarah Chen"
        assert result["recurrence"] == "FREQ=WEEKLY;BYDAY=TU"
        assert result["created"] == "2026-01-15T10:00:00.000Z"

    async def test_strips_prefix(self, adapter: GcalAdapter):
        """Event ID prefix is stripped before API call."""
        mock_service = MagicMock()
        mock_service.events.return_value.get.return_value.execute.return_value = {
            "id": "evt1", "summary": "X",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
        }
        adapter._service = mock_service

        await adapter.read_event("gc:evt1")

        call_args = mock_service.events.return_value.get.call_args
        assert call_args[1]["eventId"] == "evt1"


class TestListCalendars:
    async def test_filters_freebusy_only(self, adapter: GcalAdapter):
        """freeBusyReader calendars are excluded."""
        mock_service = MagicMock()
        mock_service.calendarList.return_value.list.return_value.execute.return_value = {
            "items": [
                {"id": "primary", "summary": "Main", "accessRole": "owner", "timeZone": "Europe/Amsterdam"},
                {"id": "holidays", "summary": "Holidays", "accessRole": "reader", "timeZone": "Europe/Amsterdam"},
                {"id": "freebusy", "summary": "Busy", "accessRole": "freeBusyReader", "timeZone": "UTC"},
            ],
        }
        adapter._service = mock_service

        result = await adapter.list_calendars()

        assert len(result) == 2
        assert result[0]["id"] == "primary"
        assert result[1]["id"] == "holidays"

    async def test_pagination(self, adapter: GcalAdapter):
        """Follows nextPageToken."""
        mock_service = MagicMock()
        page1 = {"items": [{"id": "c1", "summary": "A", "accessRole": "owner", "timeZone": "UTC"}], "nextPageToken": "tok"}
        page2 = {"items": [{"id": "c2", "summary": "B", "accessRole": "owner", "timeZone": "UTC"}]}
        mock_service.calendarList.return_value.list.return_value.execute.side_effect = [page1, page2]
        adapter._service = mock_service

        result = await adapter.list_calendars()
        assert len(result) == 2

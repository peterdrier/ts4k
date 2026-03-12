"""Tests for the O365 Calendar adapter."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from ts4k.adapters.o365cal import O365CalAdapter, O365CalAdapterConfig, graph_recurrence_to_human
from ts4k.core.levels import AccessLevel


def _mock_response(data: dict, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response with JSON data."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def config(tmp_path: Path) -> O365CalAdapterConfig:
    return O365CalAdapterConfig(
        email="user@contoso.com",
        client_id="test-client-id",
        tenant_id="test-tenant-id",
        calendar_id="default",
        calendar_name="Work Calendar",
        timezone="Europe/Amsterdam",
        config_dir=tmp_path,
        level="readonly",
    )


@pytest.fixture
def adapter(config: O365CalAdapterConfig) -> O365CalAdapter:
    a = O365CalAdapter(config, prefix="oc")
    a._client = AsyncMock(spec=httpx.AsyncClient)
    return a


class TestConstruction:
    def test_prefix(self, adapter: O365CalAdapter):
        assert adapter.source_prefix == "oc"

    def test_access_level(self, adapter: O365CalAdapter):
        assert adapter.access_level == AccessLevel.READONLY


class TestMessagingStubs:
    async def test_whatsnew_returns_empty(self, adapter: O365CalAdapter):
        assert await adapter.whatsnew() == []

    async def test_list_messages_returns_empty(self, adapter: O365CalAdapter):
        assert await adapter.list_messages() == []

    async def test_read_message_raises(self, adapter: O365CalAdapter):
        with pytest.raises(NotImplementedError):
            await adapter.read_message("oc:123")

    async def test_read_thread_raises(self, adapter: O365CalAdapter):
        with pytest.raises(NotImplementedError):
            await adapter.read_thread("oc:t123")


class TestListEvents:
    async def test_basic_timed_event(self, adapter: O365CalAdapter):
        graph_event = {
            "id": "AAMkEvt1",
            "subject": "Standup",
            "start": {"dateTime": "2026-03-11T09:00:00.0000000", "timeZone": "Europe/Amsterdam"},
            "end": {"dateTime": "2026-03-11T09:30:00.0000000", "timeZone": "Europe/Amsterdam"},
            "isAllDay": False,
            "isCancelled": False,
            "organizer": {"emailAddress": {"name": "Sarah", "address": "sarah@contoso.com"}},
            "attendees": [
                {"emailAddress": {"address": "sarah@contoso.com"}, "status": {"response": "accepted"}},
                {"emailAddress": {"address": "user@contoso.com"}, "status": {"response": "accepted"}},
                {"emailAddress": {"address": "mike@contoso.com"}, "status": {"response": "tentativelyAccepted"}},
            ],
            "location": {"displayName": "Room 4A"},
            "responseStatus": {"response": "accepted"},
        }
        adapter._client.get = AsyncMock(return_value=_mock_response({"value": [graph_event]}))

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00",
            time_max="2026-03-12T00:00:00",
        )

        assert len(result) == 1
        evt = result[0]
        assert evt["id"] == "oc:AAMkEvt1"
        assert evt["source"] == "oc"
        assert evt["title"] == "Standup"
        assert evt["all_day"] is False
        assert evt["duration_minutes"] == 30
        assert evt["organizer"] == "sarah@contoso.com"
        assert evt["attendees_summary"] == "3 people"
        assert evt["your_status"] == "accepted"
        assert evt["location"] == "Room 4A"
        assert evt["recurring_event_id"] is None

    async def test_all_day_event(self, adapter: O365CalAdapter):
        graph_event = {
            "id": "AAMkEvt2",
            "subject": "Vacation",
            "start": {"dateTime": "2026-03-17", "timeZone": "Europe/Amsterdam"},
            "end": {"dateTime": "2026-03-22", "timeZone": "Europe/Amsterdam"},
            "isAllDay": True,
            "isCancelled": False,
            "location": {"displayName": ""},
            "responseStatus": {"response": "organizer"},
        }
        adapter._client.get = AsyncMock(return_value=_mock_response({"value": [graph_event]}))

        result = await adapter.list_events(
            time_min="2026-03-17T00:00:00",
            time_max="2026-03-23T00:00:00",
        )

        evt = result[0]
        assert evt["all_day"] is True
        assert evt["start"] == "2026-03-17"
        assert evt["end"] == "2026-03-22"

    async def test_recurring_event_has_series_master_id(self, adapter: O365CalAdapter):
        graph_event = {
            "id": "AAMkEvt1_instance",
            "seriesMasterId": "AAMkEvt1_master",
            "subject": "Standup",
            "start": {"dateTime": "2026-03-11T09:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-11T09:30:00.0000000", "timeZone": "UTC"},
            "isAllDay": False,
            "isCancelled": False,
            "location": {"displayName": ""},
            "responseStatus": {"response": "organizer"},
        }
        adapter._client.get = AsyncMock(return_value=_mock_response({"value": [graph_event]}))

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00",
            time_max="2026-03-12T00:00:00",
        )

        assert result[0]["recurring_event_id"] == "oc:AAMkEvt1_master"

    async def test_pagination(self, adapter: O365CalAdapter):
        page1_resp = _mock_response({
            "value": [{
                "id": "e1", "subject": "A",
                "start": {"dateTime": "2026-03-11T09:00:00", "timeZone": "UTC"},
                "end": {"dateTime": "2026-03-11T10:00:00", "timeZone": "UTC"},
                "isAllDay": False, "isCancelled": False,
                "location": {"displayName": ""},
                "responseStatus": {"response": "organizer"},
            }],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendarView?$skip=1",
        })
        page2_resp = _mock_response({
            "value": [{
                "id": "e2", "subject": "B",
                "start": {"dateTime": "2026-03-11T11:00:00", "timeZone": "UTC"},
                "end": {"dateTime": "2026-03-11T12:00:00", "timeZone": "UTC"},
                "isAllDay": False, "isCancelled": False,
                "location": {"displayName": ""},
                "responseStatus": {"response": "organizer"},
            }],
        })
        adapter._client.get = AsyncMock(side_effect=[page1_resp, page2_resp])

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00",
            time_max="2026-03-12T00:00:00",
        )

        assert len(result) == 2
        assert result[0]["title"] == "A"
        assert result[1]["title"] == "B"

    async def test_declined_event_included(self, adapter: O365CalAdapter):
        graph_event = {
            "id": "AAMkEvt3",
            "subject": "Skipped Meeting",
            "start": {"dateTime": "2026-03-11T14:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-11T15:00:00", "timeZone": "UTC"},
            "isAllDay": False,
            "isCancelled": False,
            "location": {"displayName": ""},
            "responseStatus": {"response": "declined"},
        }
        adapter._client.get = AsyncMock(return_value=_mock_response({"value": [graph_event]}))

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00",
            time_max="2026-03-12T00:00:00",
        )

        assert result[0]["your_status"] == "declined"


class TestReadEvent:
    async def test_full_detail(self, adapter: O365CalAdapter):
        graph_event = {
            "id": "AAMkEvt1",
            "subject": "Budget Review",
            "start": {"dateTime": "2026-03-11T11:00:00.0000000", "timeZone": "Europe/Amsterdam"},
            "end": {"dateTime": "2026-03-11T12:00:00.0000000", "timeZone": "Europe/Amsterdam"},
            "isAllDay": False,
            "isCancelled": False,
            "organizer": {"emailAddress": {"name": "Sarah Chen", "address": "sarah@contoso.com"}},
            "body": {"contentType": "text", "content": "Review Q1 numbers."},
            "location": {"displayName": "Room 4A"},
            "attendees": [
                {"emailAddress": {"name": "Sarah Chen", "address": "sarah@contoso.com"}, "status": {"response": "accepted"}},
                {"emailAddress": {"name": "User", "address": "user@contoso.com"}, "status": {"response": "accepted"}},
            ],
            "recurrence": {
                "pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["tuesday"]},
                "range": {"type": "noEnd"},
            },
            "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/xyz"},
            "createdDateTime": "2026-01-15T10:00:00Z",
            "lastModifiedDateTime": "2026-03-10T14:30:00Z",
            "responseStatus": {"response": "accepted"},
        }
        adapter._client.get = AsyncMock(return_value=_mock_response(graph_event))

        result = await adapter.read_event("oc:AAMkEvt1")

        assert result["description"] == "Review Q1 numbers."
        assert result["meeting_link"] == "https://teams.microsoft.com/l/meetup-join/xyz"
        assert len(result["attendees"]) == 2
        assert result["attendees"][0]["name"] == "Sarah Chen"
        assert result["recurrence_summary"] == "weekly on Tue"
        assert result["created"] == "2026-01-15T10:00:00Z"

    async def test_strips_prefix(self, adapter: O365CalAdapter):
        graph_event = {
            "id": "AAMkEvt1", "subject": "X",
            "start": {"dateTime": "2026-03-11T09:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-11T10:00:00", "timeZone": "UTC"},
            "isAllDay": False, "isCancelled": False,
            "location": {"displayName": ""},
            "responseStatus": {"response": "organizer"},
        }
        adapter._client.get = AsyncMock(return_value=_mock_response(graph_event))

        await adapter.read_event("oc:AAMkEvt1")

        call_args = adapter._client.get.call_args
        assert "oc:" not in str(call_args)


class TestListCalendars:
    async def test_returns_calendars(self, adapter: O365CalAdapter):
        graph_resp = {
            "value": [
                {"id": "AAMkCal1", "name": "Calendar", "isDefaultCalendar": True, "canEdit": True, "owner": {"name": "User", "address": "user@contoso.com"}},
                {"id": "AAMkCal2", "name": "Team Events", "isDefaultCalendar": False, "canEdit": True, "owner": {"name": "User", "address": "user@contoso.com"}},
            ],
        }
        adapter._client.get = AsyncMock(return_value=_mock_response(graph_resp))

        result = await adapter.list_calendars()

        assert len(result) == 2
        assert result[0]["id"] == "AAMkCal1"
        assert result[0]["summary"] == "Calendar"
        assert result[0]["primary"] is True
        assert result[1]["primary"] is False

    async def test_pagination(self, adapter: O365CalAdapter):
        page1 = _mock_response({
            "value": [{"id": "c1", "name": "A", "isDefaultCalendar": True, "canEdit": True}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendars?$skip=1",
        })
        page2 = _mock_response({
            "value": [{"id": "c2", "name": "B", "isDefaultCalendar": False, "canEdit": True}],
        })
        adapter._client.get = AsyncMock(side_effect=[page1, page2])

        result = await adapter.list_calendars()
        assert len(result) == 2


class TestGraphRecurrenceToHuman:
    def test_daily(self):
        assert graph_recurrence_to_human({"pattern": {"type": "daily", "interval": 1}}) == "daily"

    def test_weekly_with_days(self):
        r = {"pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["monday", "thursday"]}}
        assert graph_recurrence_to_human(r) == "weekly on Mon+Thu"

    def test_weekly_no_days(self):
        r = {"pattern": {"type": "weekly", "interval": 1, "daysOfWeek": []}}
        assert graph_recurrence_to_human(r) == "weekly"

    def test_monthly(self):
        r = {"pattern": {"type": "absoluteMonthly", "interval": 1}}
        assert graph_recurrence_to_human(r) == "monthly"

    def test_yearly(self):
        r = {"pattern": {"type": "absoluteYearly", "interval": 1}}
        assert graph_recurrence_to_human(r) == "yearly"

    def test_biweekly(self):
        r = {"pattern": {"type": "weekly", "interval": 2, "daysOfWeek": ["tuesday"]}}
        assert graph_recurrence_to_human(r) == "every 2 weekly on Tue"

    def test_none_returns_empty(self):
        assert graph_recurrence_to_human(None) == ""

    def test_empty_dict_returns_empty(self):
        assert graph_recurrence_to_human({}) == ""

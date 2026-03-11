"""Tests for calendar command functions."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from ts4k.commands import (
    cal_today,
    cal_tomorrow,
    cal_week,
    cal_next,
    cal_range,
    cal_event,
    cal_list_calendars,
    cal_create,
    cal_update,
    cal_rsvp,
    _cal_time_bounds,
    _get_cal_timezone,
    _cal_fetch_events,
)
from ts4k.state import sources


@pytest.fixture
def mock_sources(tmp_path, monkeypatch):
    """Set up mock sources with a gcal source."""
    sources_file = tmp_path / "sources.json"
    sources_file.write_text(
        '{"gc": {"provider": "gcal", "email": "test@gmail.com", '
        '"calendar_id": "primary", "calendar_name": "Main", '
        '"timezone": "UTC", "level": "readonly"}}'
    )
    monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)


@pytest.fixture
def mock_events():
    """Standard list of mock events for reuse."""
    return [
        {
            "id": "gc:e1",
            "source": "gc",
            "title": "Standup",
            "start": "2026-03-11T09:00:00Z",
            "end": "2026-03-11T09:30:00Z",
            "all_day": False,
            "duration_minutes": 30,
            "location": "Zoom",
            "attendees_summary": "3 people",
            "status": "confirmed",
            "your_status": None,
            "recurring_event_id": None,
        },
    ]


class TestCalTimeBounds:
    def test_returns_iso_strings(self):
        time_min, time_max = _cal_time_bounds(day_offset=0, days=1, timezone="UTC")
        assert "T00:00:00" in time_min
        assert "T" in time_max

    def test_day_offset_shifts_start(self):
        today_min, _ = _cal_time_bounds(day_offset=0, days=1, timezone="UTC")
        tomorrow_min, _ = _cal_time_bounds(day_offset=1, days=1, timezone="UTC")
        assert today_min < tomorrow_min

    def test_invalid_timezone_falls_back_to_utc(self):
        time_min, time_max = _cal_time_bounds(timezone="Not/A/Zone")
        assert "+00:00" in time_min


class TestGetCalTimezone:
    def test_returns_configured_tz(self, mock_sources):
        tz = _get_cal_timezone(None)
        assert tz == "UTC"

    def test_returns_utc_when_no_gcal_sources(self, tmp_path, monkeypatch):
        sources_file = tmp_path / "sources.json"
        sources_file.write_text('{"g": {"provider": "gmail", "email": "x@y.com"}}')
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)
        tz = _get_cal_timezone(None)
        assert tz == "UTC"

    def test_returns_specific_source_tz(self, tmp_path, monkeypatch):
        sources_file = tmp_path / "sources.json"
        sources_file.write_text(
            '{"gc": {"provider": "gcal", "email": "a@b.com", "calendar_id": "primary", '
            '"timezone": "Europe/Amsterdam"}, '
            '"gc2": {"provider": "gcal", "email": "a@b.com", "calendar_id": "work", '
            '"timezone": "America/New_York"}}'
        )
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)
        tz = _get_cal_timezone("gc2")
        assert tz == "America/New_York"


class TestCalToday:
    @pytest.mark.asyncio
    async def test_returns_command_result(self, mock_sources, mock_events):
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.list_events = AsyncMock(return_value=mock_events)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_today(source=None, fmt="pipe")

        assert result.output
        assert "Standup" in result.output
        assert result.messages_processed == 1

    @pytest.mark.asyncio
    async def test_no_events(self, mock_sources):
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.list_events = AsyncMock(return_value=[])
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_today(source=None, fmt="pipe")

        assert "No events" in result.output
        assert result.messages_processed == 0


class TestCalTomorrow:
    @pytest.mark.asyncio
    async def test_returns_command_result(self, mock_sources, mock_events):
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.list_events = AsyncMock(return_value=mock_events)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_tomorrow(source=None, fmt="pipe")

        assert result.output
        assert "Standup" in result.output


class TestCalWeek:
    @pytest.mark.asyncio
    async def test_returns_events(self, mock_sources, mock_events):
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.list_events = AsyncMock(return_value=mock_events)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_week(source=None, fmt="pipe")

        assert result.output
        assert "Standup" in result.output

    @pytest.mark.asyncio
    async def test_monday_to_sunday_range(self, mock_sources):
        """cal_week should compute Mon-Sun, not next 7 days."""
        captured_args = {}

        async def capture_list_events(**kwargs):
            captured_args.update(kwargs)
            return []

        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.list_events = AsyncMock(side_effect=capture_list_events)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            await cal_week(source=None, fmt="pipe")

        # Verify the time_min is a Monday (weekday 0) at midnight
        from datetime import datetime
        time_min = captured_args.get("time_min", "")
        dt = datetime.fromisoformat(time_min)
        assert dt.weekday() == 0, f"Expected Monday (0), got {dt.weekday()}"
        assert dt.hour == 0 and dt.minute == 0


class TestCalNext:
    @pytest.mark.asyncio
    async def test_returns_limited_events(self, mock_sources, mock_events):
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.list_events = AsyncMock(return_value=mock_events)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_next(source=None, count=5, fmt="pipe")

        assert result.output
        assert "Standup" in result.output


class TestCalRange:
    @pytest.mark.asyncio
    async def test_returns_events_in_range(self, mock_sources, mock_events):
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.list_events = AsyncMock(return_value=mock_events)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_range(
                source=None, from_date="2026-03-11", to_date="2026-03-12", fmt="pipe"
            )

        assert result.output
        assert "Standup" in result.output


class TestCalEvent:
    @pytest.mark.asyncio
    async def test_returns_detail(self, mock_sources):
        mock_event = {
            "id": "gc:e1",
            "source": "gc",
            "title": "Budget Review",
            "start": "2026-03-11T11:00:00Z",
            "end": "2026-03-11T12:00:00Z",
            "all_day": False,
            "duration_minutes": 60,
            "location": "Room 4A",
            "organizer": "sarah@work.com",
            "status": "confirmed",
            "your_status": "accepted",
            "attendees": [
                {"name": "Sarah", "email": "sarah@work.com", "status": "accepted"}
            ],
            "description": "Review Q1.",
            "meeting_link": "",
            "recurrence_summary": "",
            "recurring_event_id": None,
        }
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.read_event = AsyncMock(return_value=mock_event)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_event(ref_or_id="gc:e1", source="gc", fmt="pipe")

        assert "Budget Review" in result

    @pytest.mark.asyncio
    async def test_ref_resolution(self, mock_sources):
        from ts4k.state.refs import RefTable

        mock_event = {
            "id": "gc:e1",
            "source": "gc",
            "title": "Test Event",
            "start": "2026-03-11T09:00:00Z",
            "end": "2026-03-11T10:00:00Z",
            "all_day": False,
            "duration_minutes": 60,
            "location": "",
            "organizer": "",
            "status": "confirmed",
            "your_status": None,
            "attendees": [],
            "description": "",
            "meeting_link": "",
            "recurrence_summary": "",
            "recurring_event_id": None,
        }
        ref_table = RefTable()
        ref_table.assign([{"id": "gc:e1"}])

        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.read_event = AsyncMock(return_value=mock_event)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_event(
                ref_or_id="1", source="gc", fmt="pipe", ref_table=ref_table
            )

        assert "Test Event" in result

    @pytest.mark.asyncio
    async def test_no_gcal_source(self, tmp_path, monkeypatch):
        sources_file = tmp_path / "sources.json"
        sources_file.write_text('{"g": {"provider": "gmail", "email": "x@y.com"}}')
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)

        result = await cal_event(ref_or_id="gc:e1", source=None, fmt="pipe")
        assert "Error" in result


class TestCalCreate:
    @pytest.mark.asyncio
    async def test_creates_event(self, mock_sources, tmp_path, monkeypatch):
        # Need a source with draft level for create
        sources_file = tmp_path / "sources.json"
        sources_file.write_text(
            '{"gc": {"provider": "gcal", "email": "test@gmail.com", '
            '"calendar_id": "primary", "calendar_name": "Main", '
            '"timezone": "UTC", "level": "draft"}}'
        )
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)

        created_event = {
            "id": "gc:new1",
            "source": "gc",
            "title": "New Event",
            "start": "2026-03-11T09:00:00Z",
            "end": "2026-03-11T10:00:00Z",
            "all_day": False,
            "duration_minutes": 60,
            "location": "",
            "organizer": "",
            "attendees_summary": "",
            "status": "confirmed",
            "your_status": None,
            "recurring_event_id": None,
        }
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.create_event = AsyncMock(return_value=created_event)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_create(
                source="gc",
                title="New Event",
                start="2026-03-11T09:00:00Z",
                end="2026-03-11T10:00:00Z",
                description=None,
                location=None,
                attendees=None,
            )

        assert "Created" in result
        assert "New Event" in result

    @pytest.mark.asyncio
    async def test_error_on_non_gcal_source(self, tmp_path, monkeypatch):
        sources_file = tmp_path / "sources.json"
        sources_file.write_text('{"g": {"provider": "gmail", "email": "x@y.com"}}')
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)

        result = await cal_create(
            source="g",
            title="Test",
            start="2026-03-11T09:00:00Z",
            end="2026-03-11T10:00:00Z",
            description=None,
            location=None,
            attendees=None,
        )
        assert "Error" in result


class TestCalUpdate:
    @pytest.mark.asyncio
    async def test_updates_event(self, mock_sources, tmp_path, monkeypatch):
        sources_file = tmp_path / "sources.json"
        sources_file.write_text(
            '{"gc": {"provider": "gcal", "email": "test@gmail.com", '
            '"calendar_id": "primary", "calendar_name": "Main", '
            '"timezone": "UTC", "level": "modify"}}'
        )
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)

        updated_event = {
            "id": "gc:e1",
            "source": "gc",
            "title": "Updated Title",
            "start": "2026-03-11T09:00:00Z",
            "end": "2026-03-11T10:00:00Z",
            "all_day": False,
            "duration_minutes": 60,
            "location": "",
            "organizer": "",
            "attendees_summary": "",
            "status": "confirmed",
            "your_status": None,
            "recurring_event_id": None,
        }
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.update_event = AsyncMock(return_value=updated_event)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_update("gc:e1", source="gc", title="Updated Title")

        assert "Updated" in result
        assert "Updated Title" in result


class TestCalRsvp:
    @pytest.mark.asyncio
    async def test_rsvp_accepted(self, mock_sources, tmp_path, monkeypatch):
        sources_file = tmp_path / "sources.json"
        sources_file.write_text(
            '{"gc": {"provider": "gcal", "email": "test@gmail.com", '
            '"calendar_id": "primary", "calendar_name": "Main", '
            '"timezone": "UTC", "level": "modify"}}'
        )
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)

        rsvp_event = {
            "id": "gc:e1",
            "source": "gc",
            "title": "Team Meeting",
            "start": "2026-03-11T09:00:00Z",
            "end": "2026-03-11T10:00:00Z",
            "all_day": False,
            "duration_minutes": 60,
            "location": "",
            "organizer": "",
            "attendees_summary": "",
            "status": "confirmed",
            "your_status": "accepted",
            "recurring_event_id": None,
        }
        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.rsvp = AsyncMock(return_value=rsvp_event)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await cal_rsvp("gc:e1", source="gc", status="accepted")

        assert "RSVP accepted" in result
        assert "Team Meeting" in result


class TestCalFetchEventsMultiSource:
    @pytest.mark.asyncio
    async def test_merges_and_sorts(self, tmp_path, monkeypatch):
        """Events from multiple gcal sources are merged and sorted by start."""
        sources_file = tmp_path / "sources.json"
        sources_file.write_text(
            '{"gc": {"provider": "gcal", "email": "a@b.com", "calendar_id": "primary", '
            '"calendar_name": "Main", "timezone": "UTC", "level": "readonly"}, '
            '"gc2": {"provider": "gcal", "email": "a@b.com", "calendar_id": "work", '
            '"calendar_name": "Work", "timezone": "UTC", "level": "readonly"}}'
        )
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)

        events_1 = [
            {"id": "gc:e2", "source": "gc", "title": "Later", "start": "2026-03-11T10:00:00Z", "end": "2026-03-11T11:00:00Z", "all_day": False, "duration_minutes": 60, "location": "", "attendees_summary": "", "status": "confirmed", "your_status": None, "recurring_event_id": None},
        ]
        events_2 = [
            {"id": "gc2:e1", "source": "gc2", "title": "Earlier", "start": "2026-03-11T08:00:00Z", "end": "2026-03-11T09:00:00Z", "all_day": False, "duration_minutes": 60, "location": "", "attendees_summary": "", "status": "confirmed", "your_status": None, "recurring_event_id": None},
        ]

        call_count = [0]

        async def mock_list_events(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return events_1
            return events_2

        with patch("ts4k.commands.GcalAdapter") as MockAdapter:
            instance = AsyncMock()
            instance.list_events = AsyncMock(side_effect=mock_list_events)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockAdapter.return_value = instance

            result = await _cal_fetch_events(
                source=None,
                time_min="2026-03-11T00:00:00Z",
                time_max="2026-03-12T00:00:00Z",
            )

        assert len(result) == 2
        # Earlier event should come first after sorting
        assert result[0]["title"] == "Earlier"
        assert result[1]["title"] == "Later"

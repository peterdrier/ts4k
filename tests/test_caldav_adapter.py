"""Tests for the CalDAV calendar adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from icalendar import Calendar as IcsCalendar

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


def _mk_caldav_event(ics: str) -> MagicMock:
    """Build a fake caldav Event exposing .icalendar_component."""
    comp = IcsCalendar.from_ical(ics).walk("VEVENT")[0]
    obj = MagicMock()
    obj.icalendar_component = comp
    return obj


TIMED_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:abc123@icloud.com
SUMMARY:Dentist
DTSTART;TZID=Europe/Amsterdam:20260730T140000
DTEND;TZID=Europe/Amsterdam:20260730T150000
ORGANIZER:mailto:org@example.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:test@icloud.com
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:other@example.com
STATUS:CONFIRMED
LOCATION:Main St 1
END:VEVENT
END:VCALENDAR
"""

ALLDAY_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:allday1
SUMMARY:Holiday
DTSTART;VALUE=DATE:20260730
DTEND;VALUE=DATE:20260731
END:VEVENT
END:VCALENDAR
"""

FLOATING_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:float1
SUMMARY:Floating
DTSTART:20260730T090000
DTEND:20260730T093000
END:VEVENT
END:VCALENDAR
"""

INSTANCE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:rec1@icloud.com
SUMMARY:Standup
DTSTART;TZID=Europe/Amsterdam:20260806T140000
DTEND;TZID=Europe/Amsterdam:20260806T141500
RECURRENCE-ID;TZID=Europe/Amsterdam:20260806T140000
END:VEVENT
END:VCALENDAR
"""

FOREIGN_TZ_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:foreign1
SUMMARY:NY Sync
DTSTART;TZID=America/New_York:20260730T090000
DTEND;TZID=America/New_York:20260730T100000
END:VEVENT
END:VCALENDAR
"""


class TestNormalization:
    def test_timed_event(self, adapter: CaldavAdapter):
        e = adapter._normalize_component(_mk_caldav_event(TIMED_ICS).icalendar_component)
        assert e["id"] == "cc:abc123@icloud.com"
        assert e["source"] == "cc"
        assert e["title"] == "Dentist"
        assert e["start"].startswith("2026-07-30T14:00:00")
        assert e["all_day"] is False
        assert e["duration_minutes"] == 60
        assert e["location"] == "Main St 1"
        assert e["organizer"] == "org@example.com"
        assert e["attendees_summary"] == "2 people"
        assert e["status"] == "confirmed"
        assert e["your_status"] == "accepted"
        assert e["recurring_event_id"] is None

    def test_all_day_event(self, adapter: CaldavAdapter):
        e = adapter._normalize_component(_mk_caldav_event(ALLDAY_ICS).icalendar_component)
        assert e["all_day"] is True
        assert e["start"] == "2026-07-30"
        assert e["end"] == "2026-07-31"
        assert e["duration_minutes"] is None

    def test_floating_time_gets_config_timezone(self, adapter: CaldavAdapter):
        e = adapter._normalize_component(_mk_caldav_event(FLOATING_ICS).icalendar_component)
        # Europe/Amsterdam on 2026-07-30 is UTC+2
        assert e["start"] == "2026-07-30T09:00:00+02:00"
        assert e["duration_minutes"] == 30

    def test_recurring_instance_ids(self, adapter: CaldavAdapter):
        e = adapter._normalize_component(_mk_caldav_event(INSTANCE_ICS).icalendar_component)
        assert e["recurring_event_id"] == "cc:rec1@icloud.com"
        assert e["id"].startswith("cc:rec1@icloud.com::2026-08-06T14:00:00")

    def test_foreign_timezone_normalized_to_config_tz(self, adapter: CaldavAdapter):
        # 09:00 EDT (America/New_York) on 2026-07-30 == 15:00 CEST (Europe/Amsterdam)
        e = adapter._normalize_component(_mk_caldav_event(FOREIGN_TZ_ICS).icalendar_component)
        assert e["start"] == "2026-07-30T15:00:00+02:00"
        assert e["duration_minutes"] == 60


class TestListEvents:
    async def test_search_called_with_expand_and_sorted(self, adapter: CaldavAdapter):
        adapter._calendar.search.return_value = [
            _mk_caldav_event(ALLDAY_ICS),   # starts 2026-07-30 (date sorts first)
            _mk_caldav_event(TIMED_ICS),    # starts 2026-07-30T14:00
        ]
        events = await adapter.list_events(
            "2026-07-30T00:00:00+02:00", "2026-07-31T00:00:00+02:00"
        )
        assert [e["title"] for e in events] == ["Holiday", "Dentist"]
        kwargs = adapter._calendar.search.call_args.kwargs
        assert kwargs["event"] is True
        assert kwargs["expand"] is True

    async def test_count_caps_results(self, adapter: CaldavAdapter):
        adapter._calendar.search.return_value = [
            _mk_caldav_event(TIMED_ICS) for _ in range(5)
        ]
        events = await adapter.list_events(
            "2026-07-30T00:00:00", "2026-07-31T00:00:00", count=2
        )
        assert len(events) == 2


DETAIL_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:det1@icloud.com
SUMMARY:Planning
DTSTART;TZID=Europe/Amsterdam:20260730T100000
DTEND;TZID=Europe/Amsterdam:20260730T110000
DESCRIPTION:Quarterly planning session
URL:https://meet.example.com/xyz
ATTENDEE;CN=Alice;PARTSTAT=ACCEPTED:mailto:alice@example.com
ATTENDEE;PARTSTAT=DECLINED:mailto:test@icloud.com
RRULE:FREQ=WEEKLY;BYDAY=TH
CREATED:20260101T000000Z
LAST-MODIFIED:20260615T120000Z
END:VEVENT
END:VCALENDAR
"""


class TestReadEvent:
    async def test_full_detail(self, adapter: CaldavAdapter):
        adapter._calendar.event_by_uid.return_value = _mk_caldav_event(DETAIL_ICS)
        e = await adapter.read_event("cc:det1@icloud.com")
        assert e["title"] == "Planning"
        assert e["description"] == "Quarterly planning session"
        assert e["meeting_link"] == "https://meet.example.com/xyz"
        assert e["recurrence"] == "FREQ=WEEKLY;BYDAY=TH"
        assert e["recurrence_summary"] == "weekly on Thu"
        assert e["attendees"] == [
            {"name": "Alice", "email": "alice@example.com", "status": "accepted"},
            {"name": "test@icloud.com", "email": "test@icloud.com", "status": "declined"},
        ]
        assert e["created"] == "2026-01-01T00:00:00+00:00"
        assert e["updated"] == "2026-06-15T12:00:00+00:00"
        adapter._calendar.event_by_uid.assert_called_once_with("det1@icloud.com")

    async def test_instance_id_fetches_master(self, adapter: CaldavAdapter):
        adapter._calendar.event_by_uid.return_value = _mk_caldav_event(DETAIL_ICS)
        await adapter.read_event("cc:det1@icloud.com::2026-08-06T10:00:00+02:00")
        adapter._calendar.event_by_uid.assert_called_once_with("det1@icloud.com")


class TestListCalendars:
    async def test_lists_principal_calendars(self, adapter: CaldavAdapter):
        c1 = MagicMock()
        c1.url = "https://caldav.icloud.com/123/calendars/home/"
        c1.name = "Home"
        c2 = MagicMock()
        c2.url = "https://caldav.icloud.com/123/calendars/work/"
        c2.name = "Work"
        adapter._principal.calendars.return_value = [c1, c2]
        cals = await adapter.list_calendars()
        assert cals == [
            {"id": "https://caldav.icloud.com/123/calendars/home/", "summary": "Home",
             "access_role": "owner", "timezone": "Europe/Amsterdam", "primary": False},
            {"id": "https://caldav.icloud.com/123/calendars/work/", "summary": "Work",
             "access_role": "owner", "timezone": "Europe/Amsterdam", "primary": False},
        ]


class TestFetchByUidUrlFirst:
    """iCloud rejects UID-filter REPORTs with 412 — URL-first lookup with REPORT fallback."""

    def _obj_with_uid(self, uid: str) -> MagicMock:
        obj = MagicMock()
        comp = MagicMock()
        comp.get.return_value = uid
        obj.icalendar_component = comp
        return obj

    async def test_url_hit_with_matching_uid_skips_report(self, adapter: CaldavAdapter):
        adapter._calendar.url = "https://caldav.icloud.com/123/calendars/home/"
        adapter._calendar.event_by_url.return_value = self._obj_with_uid("det1@icloud.com")
        obj = await adapter._fetch_by_uid("det1@icloud.com")
        adapter._calendar.event_by_url.assert_called_once_with(
            "https://caldav.icloud.com/123/calendars/home/det1%40icloud.com.ics"
        )
        adapter._calendar.event_by_uid.assert_not_called()
        assert str(obj.icalendar_component.get("UID")) == "det1@icloud.com"

    async def test_url_miss_falls_back_to_report(self, adapter: CaldavAdapter):
        adapter._calendar.url = "https://caldav.icloud.com/123/calendars/home/"
        adapter._calendar.event_by_url.side_effect = Exception("404 Not Found")
        adapter._calendar.event_by_uid.return_value = self._obj_with_uid("det1@icloud.com")
        obj = await adapter._fetch_by_uid("det1@icloud.com")
        adapter._calendar.event_by_uid.assert_called_once_with("det1@icloud.com")
        assert obj is adapter._calendar.event_by_uid.return_value

    async def test_url_uid_mismatch_falls_back_to_report(self, adapter: CaldavAdapter):
        adapter._calendar.url = "https://caldav.icloud.com/123/calendars/home/"
        adapter._calendar.event_by_url.return_value = self._obj_with_uid("someone-else")
        adapter._calendar.event_by_uid.return_value = self._obj_with_uid("det1@icloud.com")
        obj = await adapter._fetch_by_uid("det1@icloud.com")
        adapter._calendar.event_by_uid.assert_called_once_with("det1@icloud.com")
        assert obj is adapter._calendar.event_by_uid.return_value

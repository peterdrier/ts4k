"""Tests for CalDAV create/update/level gating."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from icalendar import Calendar as IcsCalendar

from ts4k.adapters.caldav_cal import CaldavAdapter, CaldavAdapterConfig
from ts4k.auth.caldav import ICLOUD_CALDAV_URL, save_credentials


def _adapter(tmp_path: Path, level: str) -> CaldavAdapter:
    save_credentials("test@icloud.com", username="test@icloud.com",
                     app_password="x", server_url=ICLOUD_CALDAV_URL,
                     config_dir=tmp_path)
    config = CaldavAdapterConfig(
        email="test@icloud.com", server_url=ICLOUD_CALDAV_URL,
        calendar_id="https://caldav.icloud.com/123/calendars/home/",
        timezone="Europe/Amsterdam", config_dir=tmp_path, level=level,
    )
    a = CaldavAdapter(config, prefix="cc")
    a._principal = MagicMock()
    a._calendar = MagicMock()
    return a


def _echo_save_event(ics: str) -> MagicMock:
    """Fake caldav save_event: parse the ICS we were given and echo it back."""
    obj = MagicMock()
    obj.icalendar_component = IcsCalendar.from_ical(ics).walk("VEVENT")[0]
    return obj


class TestLevelGating:
    async def test_readonly_blocks_create(self, tmp_path: Path):
        a = _adapter(tmp_path, "readonly")
        with pytest.raises(PermissionError):
            await a.create_event("X", "2026-07-30T10:00:00", "2026-07-30T11:00:00")

    async def test_readonly_blocks_update(self, tmp_path: Path):
        a = _adapter(tmp_path, "readonly")
        with pytest.raises(PermissionError):
            await a.update_event("cc:uid1", title="Y")

    async def test_readonly_blocks_rsvp(self, tmp_path: Path):
        a = _adapter(tmp_path, "readonly")
        with pytest.raises(PermissionError):
            await a.rsvp("cc:uid1", "accepted")


class TestCreateEvent:
    async def test_timed_event(self, tmp_path: Path):
        a = _adapter(tmp_path, "draft")  # create_event with no attendees requires DRAFT
        a._calendar.save_event.side_effect = _echo_save_event
        e = await a.create_event(
            "Dinner", "2026-07-30T19:00:00", "2026-07-30T21:00:00",
            description="Birthday", location="Cafe",
        )
        assert e["title"] == "Dinner"
        assert e["start"] == "2026-07-30T19:00:00+02:00"
        assert e["duration_minutes"] == 120
        sent_ics = a._calendar.save_event.call_args.args[0]
        assert "SUMMARY:Dinner" in sent_ics
        assert "DESCRIPTION:Birthday" in sent_ics
        assert "UID:" in sent_ics

    async def test_all_day_inclusive_end_becomes_exclusive(self, tmp_path: Path):
        a = _adapter(tmp_path, "draft")  # create_event with no attendees requires DRAFT
        a._calendar.save_event.side_effect = _echo_save_event
        e = await a.create_event("Trip", "2026-08-01", "2026-08-03")
        assert e["all_day"] is True
        assert e["start"] == "2026-08-01"
        assert e["end"] == "2026-08-04"  # exclusive, mirrors gcal convention

    async def test_attendees_require_send_level(self, tmp_path: Path):
        a = _adapter(tmp_path, "modify")  # modify < send
        with pytest.raises(PermissionError):
            await a.create_event("X", "2026-07-30T10:00:00", "2026-07-30T11:00:00",
                                 attendees=["a@example.com"])

    async def test_attendees_allowed_at_send_level(self, tmp_path: Path):
        # Requires "caldav" in the SEND calendar-provider allowlist (levels.py:46);
        # without it check_level raises NotImplementedError regardless of level.
        a = _adapter(tmp_path, "send")
        a._calendar.save_event.side_effect = _echo_save_event
        e = await a.create_event("X", "2026-07-30T10:00:00", "2026-07-30T11:00:00",
                                 attendees=["a@example.com"])
        # Organizer is now added as a self-attendee alongside the invitee.
        assert e["attendees_summary"] == "2 people"

    async def test_organizer_added_when_attendees_present(self, tmp_path: Path):
        # RFC 6638 scheduling requires ORGANIZER on invite-bearing events, or
        # iCloud stores attendees inertly and never sends invites.
        a = _adapter(tmp_path, "send")
        a._calendar.save_event.side_effect = _echo_save_event
        await a.create_event("X", "2026-07-30T10:00:00", "2026-07-30T11:00:00",
                             attendees=["a@example.com"])
        sent_ics = a._calendar.save_event.call_args.args[0]
        assert "ORGANIZER" in sent_ics
        assert "mailto:test@icloud.com" in sent_ics
        parsed = IcsCalendar.from_ical(sent_ics).walk("VEVENT")[0]
        organizer = parsed.get("ORGANIZER")
        assert organizer is not None
        assert str(organizer).lower().endswith("test@icloud.com")
        attendees = parsed.get("ATTENDEE")
        if not isinstance(attendees, list):
            attendees = [attendees]
        organizer_attendee = next(
            (att for att in attendees if str(att).lower().endswith("test@icloud.com")),
            None,
        )
        assert organizer_attendee is not None
        assert str(organizer_attendee.params.get("ROLE")) == "CHAIR"
        assert str(organizer_attendee.params.get("PARTSTAT")) == "ACCEPTED"

    async def test_no_organizer_when_no_attendees(self, tmp_path: Path):
        a = _adapter(tmp_path, "draft")
        a._calendar.save_event.side_effect = _echo_save_event
        await a.create_event("X", "2026-07-30T10:00:00", "2026-07-30T11:00:00")
        sent_ics = a._calendar.save_event.call_args.args[0]
        assert "ORGANIZER" not in sent_ics


class TestUpdateEvent:
    async def test_update_title_and_start(self, tmp_path: Path):
        a = _adapter(tmp_path, "modify")
        ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:up1
SUMMARY:Old
DTSTART;TZID=Europe/Amsterdam:20260730T100000
DTEND;TZID=Europe/Amsterdam:20260730T110000
END:VEVENT
END:VCALENDAR
"""
        obj = MagicMock()
        obj.icalendar_component = IcsCalendar.from_ical(ics).walk("VEVENT")[0]
        a._calendar.event_by_uid.return_value = obj
        e = await a.update_event("cc:up1", title="New", start="2026-07-30T12:00:00")
        obj.save.assert_called_once()
        assert e["title"] == "New"
        assert e["start"] == "2026-07-30T12:00:00+02:00"


INVITE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:inv1
SUMMARY:Party
DTSTART;TZID=Europe/Amsterdam:20260730T190000
DTEND;TZID=Europe/Amsterdam:20260730T230000
ORGANIZER:mailto:org@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:test@icloud.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:other@example.com
END:VEVENT
END:VCALENDAR
"""


def _invite_obj():
    obj = MagicMock(spec=["icalendar_component", "save"])  # no accept_invite → manual path
    obj.icalendar_component = IcsCalendar.from_ical(INVITE_ICS).walk("VEVENT")[0]
    return obj


class TestRsvp:
    async def test_manual_partstat_path(self, tmp_path: Path):
        a = _adapter(tmp_path, "modify")
        obj = _invite_obj()
        a._calendar.event_by_uid.return_value = obj
        e = await a.rsvp("cc:inv1", "accepted")
        obj.save.assert_called_once()
        assert e["your_status"] == "accepted"

    async def test_invite_helper_preferred_when_available(self, tmp_path: Path):
        a = _adapter(tmp_path, "modify")
        obj = MagicMock()  # has accept_invite (MagicMock auto-attrs)
        obj.icalendar_component = IcsCalendar.from_ical(INVITE_ICS).walk("VEVENT")[0]
        a._calendar.event_by_uid.return_value = obj
        await a.rsvp("cc:inv1", "accepted")
        obj.accept_invite.assert_called_once()
        obj.save.assert_not_called()

    async def test_invite_helper_failure_falls_back_to_manual(self, tmp_path: Path):
        a = _adapter(tmp_path, "modify")
        obj = MagicMock()
        obj.icalendar_component = IcsCalendar.from_ical(INVITE_ICS).walk("VEVENT")[0]
        obj.accept_invite.side_effect = Exception("scheduling not supported")
        a._calendar.event_by_uid.return_value = obj
        e = await a.rsvp("cc:inv1", "accepted")
        obj.save.assert_called_once()
        assert e["your_status"] == "accepted"

    async def test_server_rejection_raises_actionable_runtimeerror(self, tmp_path: Path):
        a = _adapter(tmp_path, "modify")
        obj = _invite_obj()
        obj.save.side_effect = Exception("403 Forbidden")
        a._calendar.event_by_uid.return_value = obj
        with pytest.raises(RuntimeError, match="Calendar app"):
            await a.rsvp("cc:inv1", "accepted")

    async def test_not_an_attendee_raises_valueerror(self, tmp_path: Path):
        a = _adapter(tmp_path, "modify")
        ics = INVITE_ICS.replace("mailto:test@icloud.com", "mailto:someoneelse@x.com")
        obj = MagicMock(spec=["icalendar_component", "save"])
        obj.icalendar_component = IcsCalendar.from_ical(ics).walk("VEVENT")[0]
        a._calendar.event_by_uid.return_value = obj
        with pytest.raises(ValueError, match="not an attendee"):
            await a.rsvp("cc:inv1", "accepted")

    async def test_bad_status_raises_valueerror(self, tmp_path: Path):
        a = _adapter(tmp_path, "modify")
        with pytest.raises(ValueError, match="status"):
            await a.rsvp("cc:inv1", "maybe")

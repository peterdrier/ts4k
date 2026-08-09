"""Tests for calendar event formatting."""

from __future__ import annotations



class TestFormatEventsPipe:
    def test_single_day_time_only(self):
        """Events on same day show HH:MM-HH:MM time."""
        from ts4k.core.format import format_events

        events = [
            {"id": "gc:1", "source": "gc", "title": "Standup", "start": "2026-03-11T09:00:00+01:00", "end": "2026-03-11T09:30:00+01:00", "all_day": False, "duration_minutes": 30, "location": "Zoom", "attendees_summary": "3 people", "status": "confirmed", "your_status": "accepted", "recurring_event_id": None},
        ]
        result = format_events(events, fmt="pipe")
        assert "09:00-09:30" in result
        assert "30m" in result
        assert "Standup" in result

    def test_all_day_event(self):
        """All-day event shows 'all-day' with no duration."""
        from ts4k.core.format import format_events

        events = [
            {"id": "gc:2", "source": "gc", "title": "Holiday", "start": "2026-03-11", "end": "2026-03-12", "all_day": True, "duration_minutes": None, "location": "", "attendees_summary": "", "status": "confirmed", "your_status": None, "recurring_event_id": None},
        ]
        result = format_events(events, fmt="pipe")
        assert "all-day" in result
        assert "Holiday" in result

    def test_multi_day_adds_day_name(self):
        """Events spanning days show day abbreviation."""
        from ts4k.core.format import format_events

        events = [
            {"id": "gc:1", "source": "gc", "title": "Mon Meeting", "start": "2026-03-09T09:00:00+01:00", "end": "2026-03-09T10:00:00+01:00", "all_day": False, "duration_minutes": 60, "location": "", "attendees_summary": "", "status": "confirmed", "your_status": None, "recurring_event_id": None},
            {"id": "gc:2", "source": "gc", "title": "Wed Meeting", "start": "2026-03-11T09:00:00+01:00", "end": "2026-03-11T10:00:00+01:00", "all_day": False, "duration_minutes": 60, "location": "", "attendees_summary": "", "status": "confirmed", "your_status": None, "recurring_event_id": None},
        ]
        result = format_events(events, fmt="pipe")
        assert "Mon" in result
        assert "Wed" in result

    def test_declined_event_marker(self):
        """Declined events show (declined) after title."""
        from ts4k.core.format import format_events

        events = [
            {"id": "gc:1", "source": "gc", "title": "Skipped", "start": "2026-03-11T14:00:00+01:00", "end": "2026-03-11T15:00:00+01:00", "all_day": False, "duration_minutes": 60, "location": "", "attendees_summary": "", "status": "confirmed", "your_status": "declined", "recurring_event_id": None},
        ]
        result = format_events(events, fmt="pipe")
        assert "(declined)" in result

    def test_ref_table_populated(self):
        """RefTable is populated with event IDs."""
        from ts4k.core.format import format_events
        from ts4k.state.refs import RefTable

        ref_table = RefTable()
        events = [
            {"id": "gc:evt1", "source": "gc", "title": "A", "start": "2026-03-11T09:00:00Z", "end": "2026-03-11T10:00:00Z", "all_day": False, "duration_minutes": 60, "location": "", "attendees_summary": "", "status": "confirmed", "your_status": None, "recurring_event_id": None},
        ]
        format_events(events, fmt="pipe", ref_table=ref_table)
        assert ref_table.resolve("1") == "gc:evt1"


class TestFormatEventDetail:
    def test_xml_output(self):
        """Event detail produces mini-XML."""
        from ts4k.core.format import format_event_detail

        event = {
            "id": "gc:evt1", "source": "gc", "title": "Budget Review",
            "start": "2026-03-11T11:00:00+01:00", "end": "2026-03-11T12:00:00+01:00",
            "all_day": False, "duration_minutes": 60, "location": "Room 4A",
            "organizer": "sarah@work.com", "status": "confirmed",
            "your_status": "accepted",
            "attendees": [
                {"name": "Sarah Chen", "email": "sarah@work.com", "status": "accepted"},
            ],
            "description": "Review Q1 numbers.",
            "meeting_link": "https://meet.google.com/xyz",
            "recurrence_summary": "weekly on Tuesdays",
        }
        result = format_event_detail(event, ref=1, fmt="pipe")
        assert "<ev " in result
        assert "Budget Review" in result
        assert "Room 4A" in result
        assert "Sarah Chen" in result
        assert "meet.google.com" in result


class TestRecurringCollapsing:
    def test_multi_week_collapses(self):
        """Recurring events with same recurringEventId collapse in multi-week."""
        from ts4k.core.format import format_events

        events = [
            {"id": "gc:e1_0311", "source": "gc", "title": "Standup", "start": "2026-03-11T09:00:00Z", "end": "2026-03-11T09:30:00Z", "all_day": False, "duration_minutes": 30, "location": "Zoom", "attendees_summary": "3 people", "status": "confirmed", "your_status": None, "recurring_event_id": "gc:e1", "recurrence_summary": "weekly"},
            {"id": "gc:e1_0318", "source": "gc", "title": "Standup", "start": "2026-03-18T09:00:00Z", "end": "2026-03-18T09:30:00Z", "all_day": False, "duration_minutes": 30, "location": "Zoom", "attendees_summary": "3 people", "status": "confirmed", "your_status": None, "recurring_event_id": "gc:e1", "recurrence_summary": "weekly"},
            {"id": "gc:e1_0325", "source": "gc", "title": "Standup", "start": "2026-03-25T09:00:00Z", "end": "2026-03-25T09:30:00Z", "all_day": False, "duration_minutes": 30, "location": "Zoom", "attendees_summary": "3 people", "status": "confirmed", "your_status": None, "recurring_event_id": "gc:e1", "recurrence_summary": "weekly"},
        ]
        result = format_events(events, fmt="pipe", collapse_recurring=True)
        # Should have 1 row, not 3
        lines = [l for l in result.strip().split("\n") if l and not l.startswith("REF|")]
        assert len(lines) == 1
        assert "(weekly)" in lines[0]

    def test_single_day_no_collapse(self):
        """Same-day recurring instances are NOT collapsed."""
        from ts4k.core.format import format_events

        events = [
            {"id": "gc:e1_0311", "source": "gc", "title": "Standup", "start": "2026-03-11T09:00:00Z", "end": "2026-03-11T09:30:00Z", "all_day": False, "duration_minutes": 30, "location": "", "attendees_summary": "", "status": "confirmed", "your_status": None, "recurring_event_id": "gc:e1", "recurrence_summary": "weekly"},
        ]
        result = format_events(events, fmt="pipe", collapse_recurring=False)
        lines = [l for l in result.strip().split("\n") if l and not l.startswith("REF|")]
        assert len(lines) == 1

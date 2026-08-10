"""The internal-UTC convention: adapters store UTC, the format layer displays local.

These cover the two failure modes issue #54 was filed for — a cross-source
merge of calendars configured in *different* zones, and a query window
spanning a DST fallback — plus the guarantee that a single-timezone install
sees exactly the wall clock it saw before.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar as IcsCalendar

from ts4k import commands
from ts4k.adapters.caldav_cal import CaldavAdapter, CaldavAdapterConfig
from ts4k.adapters.gcal import GcalAdapter, GcalAdapterConfig
from ts4k.adapters.o365cal import O365CalAdapter, O365CalAdapterConfig
from ts4k.auth.caldav import ICLOUD_CALDAV_URL, save_credentials
from ts4k.core.format import format_event_detail, format_events


# ---------------------------------------------------------------------------
# Adapter builders — real normalizers, no transport
# ---------------------------------------------------------------------------


def _gcal(timezone: str = "Europe/Amsterdam", prefix: str = "gc") -> GcalAdapter:
    return GcalAdapter(
        GcalAdapterConfig(
            email="a@gmail.com", calendar_id="primary", calendar_name="Main",
            timezone=timezone,
        ),
        prefix=prefix,
    )


def _o365cal(timezone: str = "America/New_York", prefix: str = "oc") -> O365CalAdapter:
    return O365CalAdapter(
        O365CalAdapterConfig(
            email="a@contoso.com", client_id="cid", calendar_name="Work",
            timezone=timezone,
        ),
        prefix=prefix,
    )


def _caldav(tmp_path: Path, timezone: str = "Europe/Amsterdam") -> CaldavAdapter:
    save_credentials(
        "test@icloud.com", username="test@icloud.com", app_password="abcd-efgh",
        server_url=ICLOUD_CALDAV_URL, config_dir=tmp_path,
    )
    a = CaldavAdapter(
        CaldavAdapterConfig(
            email="test@icloud.com", server_url=ICLOUD_CALDAV_URL,
            calendar_id="https://caldav.icloud.com/1/calendars/home/",
            calendar_name="Home", timezone=timezone, config_dir=tmp_path,
        ),
        prefix="cc",
    )
    a._principal = MagicMock()
    a._calendar = MagicMock()
    return a


def _vevent(ics: str):
    return IcsCalendar.from_ical(ics).walk("VEVENT")[0]


def _gcal_raw(eid: str, title: str, start: str, end: str) -> dict:
    return {
        "id": eid, "summary": title, "status": "confirmed",
        "start": {"dateTime": start}, "end": {"dateTime": end},
    }


def _graph_raw(eid: str, title: str, start: str, end: str, zone: str) -> dict:
    return {
        "id": eid, "subject": title,
        "start": {"dateTime": start, "timeZone": zone},
        "end": {"dateTime": end, "timeZone": zone},
    }


# ---------------------------------------------------------------------------
# Every adapter stores timed events in UTC
# ---------------------------------------------------------------------------


class TestAdaptersStoreUtc:
    def test_gcal_offset_normalized_to_utc(self):
        e = _gcal()._normalize_event(
            _gcal_raw("e1", "Standup", "2026-03-11T09:00:00+01:00",
                      "2026-03-11T09:30:00+01:00")
        )
        assert e["start"] == "2026-03-11T08:00:00+00:00"
        assert e["end"] == "2026-03-11T08:30:00+00:00"
        assert e["duration_minutes"] == 30

    def test_graph_sibling_timezone_field_normalized_to_utc(self):
        """Graph puts the zone next to the dateTime, not inside it."""
        e = _o365cal()._normalize_event(
            _graph_raw("e1", "Sync", "2026-03-11T09:00:00.0000000",
                       "2026-03-11T10:00:00.0000000", "America/New_York")
        )
        assert e["start"] == "2026-03-11T13:00:00+00:00"
        assert e["end"] == "2026-03-11T14:00:00+00:00"
        assert e["duration_minutes"] == 60

    def test_caldav_tzid_normalized_to_utc(self, tmp_path: Path):
        e = _caldav(tmp_path)._normalize_component(_vevent(
            "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\n"
            "UID:u1\nSUMMARY:Dentist\n"
            "DTSTART;TZID=Europe/Amsterdam:20260311T140000\n"
            "DTEND;TZID=Europe/Amsterdam:20260311T150000\n"
            "END:VEVENT\nEND:VCALENDAR\n"
        ))
        assert e["start"] == "2026-03-11T13:00:00+00:00"
        assert e["end"] == "2026-03-11T14:00:00+00:00"


class TestAllDayEventsUnchanged:
    """All-day events are dates, not instants — no adapter may convert them."""

    def test_gcal_all_day_stays_a_date(self):
        e = _gcal()._normalize_event({
            "id": "e1", "summary": "Holiday", "status": "confirmed",
            "start": {"date": "2026-03-17"}, "end": {"date": "2026-03-22"},
        })
        assert e["all_day"] is True
        assert (e["start"], e["end"]) == ("2026-03-17", "2026-03-22")
        assert e["duration_minutes"] is None

    def test_o365cal_all_day_stays_a_date(self):
        e = _o365cal()._normalize_event({
            "id": "e1", "subject": "Holiday", "isAllDay": True,
            "start": {"dateTime": "2026-03-17T00:00:00.0000000", "timeZone": "America/New_York"},
            "end": {"dateTime": "2026-03-22T00:00:00.0000000", "timeZone": "America/New_York"},
        })
        assert e["all_day"] is True
        assert (e["start"], e["end"]) == ("2026-03-17", "2026-03-22")

    def test_caldav_all_day_stays_a_date(self, tmp_path: Path):
        e = _caldav(tmp_path)._normalize_component(_vevent(
            "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\n"
            "UID:u1\nSUMMARY:Holiday\nDTSTART;VALUE=DATE:20260317\n"
            "DTEND;VALUE=DATE:20260322\nEND:VEVENT\nEND:VCALENDAR\n"
        ))
        assert e["all_day"] is True
        assert (e["start"], e["end"]) == ("2026-03-17", "2026-03-22")

    def test_all_day_renders_the_same_in_any_display_zone(self):
        evt = {
            "id": "gc:1", "source": "gc", "title": "Holiday",
            "start": "2026-03-17", "end": "2026-03-18", "all_day": True,
            "duration_minutes": None, "location": "", "attendees_summary": "",
        }
        assert format_events([evt], tz="Pacific/Auckland") == format_events(
            [evt], tz="America/Los_Angeles"
        )


# ---------------------------------------------------------------------------
# The bug #54 was filed for
# ---------------------------------------------------------------------------


def _mock_cal_sources(monkeypatch, adapters: dict[str, object]) -> None:
    """Point _cal_fetch_events at prebuilt adapters, one per source prefix."""
    monkeypatch.setattr(
        "ts4k.state.sources.list_all",
        lambda: {
            pfx: {"provider": "gcal", "email": "a@b.com", "calendar_id": "primary"}
            for pfx in adapters
        },
    )

    def _make(prefix: str, cfg: dict):
        real = adapters[prefix]
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        mock.list_events = AsyncMock(return_value=real)
        return mock

    monkeypatch.setattr(commands, "_make_adapter", _make)


class TestCrossSourceOrdering:
    """Two sources, two configured zones, one chronological agenda."""

    @pytest.mark.asyncio
    async def test_merge_across_zones_is_chronological(self, monkeypatch):
        # Amsterdam 00:30 CET on the 11th == 23:30 UTC on the 10th.
        ams = _gcal("Europe/Amsterdam", prefix="gc")._normalize_event(
            _gcal_raw("a1", "Amsterdam midnight snack",
                      "2026-03-11T00:30:00+01:00", "2026-03-11T01:30:00+01:00")
        )
        # New York 20:00 EDT on the 10th == 00:00 UTC on the 11th — half an
        # hour LATER, though its local date string reads earlier.
        nyc = _o365cal("America/New_York", prefix="oc")._normalize_event(
            _graph_raw("n1", "New York evening call",
                       "2026-03-10T20:00:00.0000000",
                       "2026-03-10T21:00:00.0000000", "America/New_York")
        )
        assert ams["start"] == "2026-03-10T23:30:00+00:00"
        assert nyc["start"] == "2026-03-11T00:00:00+00:00"

        _mock_cal_sources(monkeypatch, {"gc": [ams], "oc": [nyc]})
        merged = await commands._cal_fetch_events(
            None, "2026-03-10T00:00:00+00:00", "2026-03-12T00:00:00+00:00"
        )

        assert [e["title"] for e in merged] == [
            "Amsterdam midnight snack",
            "New York evening call",
        ]

    @pytest.mark.asyncio
    async def test_cross_zone_merge_renders_in_one_display_zone(self, monkeypatch):
        """A merged agenda shows one wall clock, not each source's own."""
        ams = _gcal("Europe/Amsterdam", prefix="gc")._normalize_event(
            _gcal_raw("a1", "Amsterdam", "2026-03-11T15:00:00+01:00",
                      "2026-03-11T16:00:00+01:00")
        )
        nyc = _o365cal("America/New_York", prefix="oc")._normalize_event(
            _graph_raw("n1", "New York", "2026-03-11T10:00:00.0000000",
                       "2026-03-11T11:00:00.0000000", "America/New_York")
        )
        _mock_cal_sources(monkeypatch, {"gc": [ams], "oc": [nyc]})
        merged = await commands._cal_fetch_events(
            None, "2026-03-11T00:00:00+00:00", "2026-03-12T00:00:00+00:00"
        )

        # Both events are the same hour: 14:00-15:00 UTC.
        out = format_events(merged, tz="Europe/London")
        rows = [ln for ln in out.splitlines() if not ln.startswith("REF|")]
        assert len(rows) == 2
        assert all("14:00-15:00" in row for row in rows), out


class TestDstTransition:
    """Europe/Amsterdam falls back at 03:00 CEST on 2026-10-25."""

    @pytest.mark.asyncio
    async def test_fallback_day_sorts_chronologically(self, monkeypatch):
        adapter = _gcal("Europe/Amsterdam")
        # 02:30 happens twice: once at +02:00 (00:30 UTC), once at +01:00
        # (01:30 UTC).  Sorted by their local strings the repeated hour comes
        # out backwards, because "+01:00" < "+02:00".
        events = [
            adapter._normalize_event(_gcal_raw(
                "e2", "Second 02:30 (CET)",
                "2026-10-25T02:30:00+01:00", "2026-10-25T03:00:00+01:00")),
            adapter._normalize_event(_gcal_raw(
                "e1", "First 02:30 (CEST)",
                "2026-10-25T02:30:00+02:00", "2026-10-25T03:00:00+02:00")),
            adapter._normalize_event(_gcal_raw(
                "e0", "02:00 (CEST)",
                "2026-10-25T02:00:00+02:00", "2026-10-25T02:30:00+02:00")),
        ]
        assert [e["start"] for e in events] == [
            "2026-10-25T01:30:00+00:00",
            "2026-10-25T00:30:00+00:00",
            "2026-10-25T00:00:00+00:00",
        ]

        _mock_cal_sources(monkeypatch, {"gc": events})
        merged = await commands._cal_fetch_events(
            None, "2026-10-25T00:00:00+00:00", "2026-10-26T00:00:00+00:00"
        )

        assert [e["title"] for e in merged] == [
            "02:00 (CEST)",
            "First 02:30 (CEST)",
            "Second 02:30 (CET)",
        ]

    def test_fallback_day_repeated_hour_renders_local(self):
        """Both halves of the repeated hour print as 02:30 for the reader.

        The first one's *end* reads 02:00 because the clock rewinds while it
        is running — 03:00 CEST is 02:00 CET.  That is the local wall clock,
        which is what the display layer is for; the duration column still
        shows the 30 real minutes the event lasts.
        """
        adapter = _gcal("Europe/Amsterdam")
        events = [
            adapter._normalize_event(_gcal_raw(
                "e1", "First", "2026-10-25T02:30:00+02:00",
                "2026-10-25T03:00:00+02:00")),
            adapter._normalize_event(_gcal_raw(
                "e2", "Second", "2026-10-25T02:30:00+01:00",
                "2026-10-25T03:00:00+01:00")),
        ]
        out = format_events(events, tz="Europe/Amsterdam")
        rows = [ln for ln in out.splitlines() if not ln.startswith("REF|")]
        assert all(row.split("|")[2].startswith("02:30") for row in rows), out
        assert all(row.split("|")[3] == "30m" for row in rows), out


# ---------------------------------------------------------------------------
# Display timezone resolution
# ---------------------------------------------------------------------------


class TestDisplayTimezoneResolution:
    """env var, then settings.json, then the machine — one global answer."""

    @staticmethod
    def _use_settings_file(monkeypatch, tmp_path: Path, body: str | None):
        from ts4k.state import settings

        path = tmp_path / "settings.json"
        if body is not None:
            path.write_text(body, encoding="utf-8")
        monkeypatch.setattr(settings, "_SETTINGS_FILE", path)

    def test_env_var_wins_over_settings(self, monkeypatch, tmp_path: Path):
        from ts4k.core.tz import display_tzinfo

        self._use_settings_file(monkeypatch, tmp_path, '{"timezone": "Asia/Tokyo"}')
        monkeypatch.setenv("TS4K_TIMEZONE", "America/New_York")
        assert str(display_tzinfo()) == "America/New_York"

    def test_settings_file_used_when_no_env_var(self, monkeypatch, tmp_path: Path):
        from ts4k.core.tz import display_tzinfo

        self._use_settings_file(monkeypatch, tmp_path, '{"timezone": "Asia/Tokyo"}')
        monkeypatch.delenv("TS4K_TIMEZONE", raising=False)
        assert str(display_tzinfo()) == "Asia/Tokyo"

    def test_falls_back_to_the_machine_zone(self, monkeypatch, tmp_path: Path):
        from ts4k.core.tz import display_tzinfo, system_tzinfo

        self._use_settings_file(monkeypatch, tmp_path, None)
        monkeypatch.delenv("TS4K_TIMEZONE", raising=False)
        assert display_tzinfo() == system_tzinfo()

    def test_tz_env_names_the_machine_zone(self, monkeypatch):
        from ts4k.core.tz import system_tzinfo

        monkeypatch.setenv("TZ", "Asia/Tokyo")
        assert str(system_tzinfo()) == "Asia/Tokyo"

    def test_unknown_zone_falls_back_to_utc(self, monkeypatch, tmp_path: Path):
        from datetime import timezone

        from ts4k.core.tz import display_tzinfo

        self._use_settings_file(monkeypatch, tmp_path, None)
        monkeypatch.setenv("TS4K_TIMEZONE", "Not/A/Zone")
        assert display_tzinfo() is timezone.utc

    def test_malformed_settings_file_falls_back_to_the_machine(self, monkeypatch, tmp_path: Path):
        from ts4k.core.tz import display_tzinfo, system_tzinfo

        self._use_settings_file(monkeypatch, tmp_path, "{not json")
        monkeypatch.delenv("TS4K_TIMEZONE", raising=False)
        assert display_tzinfo() == system_tzinfo()


# ---------------------------------------------------------------------------
# Regression guard: the common single-timezone install
# ---------------------------------------------------------------------------


class TestSingleTimezoneUnchanged:
    """One calendar, one zone, display zone matching — times must not shift."""

    def test_listing_shows_the_calendar_wall_clock(self):
        adapter = _gcal("Europe/Amsterdam")
        events = [
            adapter._normalize_event(_gcal_raw(
                "e1", "Standup", "2026-03-11T09:00:00+01:00",
                "2026-03-11T09:30:00+01:00")),
            adapter._normalize_event(_gcal_raw(
                "e2", "Budget Review", "2026-03-11T14:00:00+01:00",
                "2026-03-11T15:00:00+01:00")),
        ]
        out = format_events(events, tz="Europe/Amsterdam")
        assert "09:00-09:30" in out
        assert "14:00-15:00" in out

    def test_detail_shows_the_calendar_wall_clock(self):
        event = _gcal("Europe/Amsterdam")._normalize_event(_gcal_raw(
            "e1", "Budget Review", "2026-03-11T11:00:00+01:00",
            "2026-03-11T12:00:00+01:00"))
        out = format_event_detail(event, ref=1, tz="Europe/Amsterdam")
        assert "<when>Wed Mar 11, 11:00-12:00 (1h)</when>" in out

    def test_caldav_listing_shows_the_calendar_wall_clock(self, tmp_path: Path):
        """The live install's source type (`cc`, iCloud) must not shift."""
        adapter = _caldav(tmp_path, "Europe/Amsterdam")
        events = [
            adapter._normalize_component(_vevent(
                "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\n"
                "UID:u1\nSUMMARY:Dentist\n"
                "DTSTART;TZID=Europe/Amsterdam:20260730T140000\n"
                "DTEND;TZID=Europe/Amsterdam:20260730T150000\n"
                "END:VEVENT\nEND:VCALENDAR\n"
            )),
            # A floating (zone-less) time means the source's own zone
            adapter._normalize_component(_vevent(
                "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//t//EN\nBEGIN:VEVENT\n"
                "UID:u2\nSUMMARY:Floating\nDTSTART:20260730T090000\n"
                "DTEND:20260730T093000\nEND:VEVENT\nEND:VCALENDAR\n"
            )),
        ]
        out = format_events(events, tz="Europe/Amsterdam")
        assert "14:00-15:00" in out
        assert "09:00-09:30" in out

    def test_day_grouping_uses_the_display_date_not_the_utc_date(self):
        """A late-evening local event must not be filed under the next day."""
        adapter = _gcal("America/New_York")
        events = [
            adapter._normalize_event(_gcal_raw(
                "e1", "Morning", "2026-03-11T09:00:00-04:00",
                "2026-03-11T10:00:00-04:00")),
            # 21:00 EDT is 01:00 UTC on the 12th — same local day, though.
            adapter._normalize_event(_gcal_raw(
                "e2", "Late show", "2026-03-11T21:00:00-04:00",
                "2026-03-11T22:00:00-04:00")),
        ]
        out = format_events(events, tz="America/New_York")
        # Single local day -> "time" mode: bare HH:MM, no day name prefix.
        assert "09:00-10:00" in out
        assert "21:00-22:00" in out
        assert "Wed" not in out


# ---------------------------------------------------------------------------
# Review follow-ups: zone fidelity at the adapter edge, display-date
# correctness at the command edge
# ---------------------------------------------------------------------------


class TestEventLevelTimezones:
    """An event may override its calendar's zone; honour it."""

    def test_gcal_floating_time_uses_the_event_zone_not_the_calendar_zone(self):
        adapter = _gcal("Europe/Amsterdam")
        raw = {
            "id": "e1", "summary": "Tokyo call", "status": "confirmed",
            "start": {"dateTime": "2026-03-11T09:00:00", "timeZone": "Asia/Tokyo"},
            "end": {"dateTime": "2026-03-11T10:00:00", "timeZone": "Asia/Tokyo"},
        }
        event = adapter._normalize_event(raw)
        # 09:00 in Tokyo is 00:00 UTC — not 08:00 UTC as Amsterdam would give.
        assert event["start"].startswith("2026-03-11T00:00")

    def test_gcal_falls_back_to_the_calendar_zone_when_absent(self):
        adapter = _gcal("Europe/Amsterdam")
        raw = {
            "id": "e1", "summary": "Local", "status": "confirmed",
            "start": {"dateTime": "2026-03-11T09:00:00"},
            "end": {"dateTime": "2026-03-11T10:00:00"},
        }
        event = adapter._normalize_event(raw)
        assert event["start"].startswith("2026-03-11T08:00")

    @pytest.mark.asyncio
    async def test_o365_pins_the_graph_response_zone_to_utc(self):
        """Unpinned, Graph may answer in Windows zone IDs ZoneInfo cannot read."""
        adapter = _o365cal("America/New_York")
        client = MagicMock()
        resp = MagicMock()
        resp.json.return_value = {"value": []}
        resp.raise_for_status = MagicMock()
        client.get = AsyncMock(return_value=resp)
        adapter._client = client

        await adapter.list_events("2026-03-11T00:00:00+00:00", "2026-03-12T00:00:00+00:00")

        headers = client.get.await_args.kwargs["headers"]
        assert headers["Prefer"] == 'outlook.timezone="UTC"'


class TestAllDayDisplayDate:
    """All-day dates are never shifted, so they must be filtered and sorted
    against the *requested* display date rather than an absolute instant."""

    @staticmethod
    def _all_day(eid: str, title: str, start: str, end: str) -> dict:
        return {
            "id": f"gc:{eid}", "source": "gc", "title": title,
            "start": start, "end": end, "all_day": True,
            "duration_minutes": None, "status": "confirmed",
        }

    @staticmethod
    def _timed(eid: str, title: str, start: str, end: str) -> dict:
        return {
            "id": f"gc:{eid}", "source": "gc", "title": title,
            "start": start, "end": end, "all_day": False,
            "duration_minutes": 60, "status": "confirmed",
        }

    def test_adjacent_day_all_day_event_is_dropped(self):
        """A Tokyo Mar 11 all-day event overlaps Mar 10 in New York."""
        zone = ZoneInfo("America/New_York")
        events = [
            self._all_day("e1", "Tokyo holiday", "2026-03-11", "2026-03-12"),
            self._all_day("e2", "NY holiday", "2026-03-10", "2026-03-11"),
        ]
        kept = commands._cal_trim_all_day(
            events,
            "2026-03-10T04:00:00+00:00",  # Mar 10 00:00 EDT
            "2026-03-11T04:00:00+00:00",  # Mar 11 00:00 EDT
            zone,
        )
        assert [e["title"] for e in kept] == ["NY holiday"]

    def test_multi_day_all_day_event_spanning_the_window_is_kept(self):
        zone = ZoneInfo("America/New_York")
        events = [self._all_day("e1", "Conference", "2026-03-09", "2026-03-13")]
        kept = commands._cal_trim_all_day(
            events, "2026-03-10T04:00:00+00:00", "2026-03-11T04:00:00+00:00", zone,
        )
        assert len(kept) == 1

    def test_same_day_end_is_treated_as_a_one_day_event(self):
        """Some sources report end == start rather than an exclusive next day."""
        zone = ZoneInfo("America/New_York")
        events = [self._all_day("e1", "Holiday", "2026-03-10", "2026-03-10")]
        kept = commands._cal_trim_all_day(
            events, "2026-03-10T04:00:00+00:00", "2026-03-11T04:00:00+00:00", zone,
        )
        assert len(kept) == 1

    def test_inclusive_end_of_day_bound_keeps_that_days_all_day_event(self):
        """`cal range` bounds at 23:59:59, not the next midnight."""
        zone = ZoneInfo("America/New_York")
        events = [self._all_day("e1", "Last day", "2026-03-12", "2026-03-13")]
        kept = commands._cal_trim_all_day(
            events,
            "2026-03-10T04:00:00+00:00",
            "2026-03-13T03:59:59+00:00",  # Mar 12 23:59:59 EDT
            zone,
        )
        assert len(kept) == 1

    def test_timed_events_are_never_filtered(self):
        zone = ZoneInfo("America/New_York")
        events = [self._timed("e1", "Call", "2026-03-10T14:00:00+00:00",
                              "2026-03-10T15:00:00+00:00")]
        kept = commands._cal_trim_all_day(
            events, "2026-03-10T04:00:00+00:00", "2026-03-11T04:00:00+00:00", zone,
        )
        assert len(kept) == 1

    def test_all_day_sorts_ahead_of_that_local_days_timed_events(self):
        """00:30+01:00 is stored on the previous UTC day but is the same
        local day, so the all-day entry must still lead it."""
        zone = ZoneInfo("Europe/Amsterdam")
        all_day = self._all_day("e1", "Holiday", "2026-03-11", "2026-03-12")
        just_after_midnight = self._timed(
            "e2", "Night call", "2026-03-10T23:30:00+00:00", "2026-03-11T00:30:00+00:00",
        )
        events = sorted(
            [just_after_midnight, all_day],
            key=lambda e: commands._cal_sort_key(e, zone),
        )
        assert [e["title"] for e in events] == ["Holiday", "Night call"]

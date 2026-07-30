"""Google Calendar adapter — direct Google Calendar API v3."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ts4k.adapters.base import BaseAdapter
from ts4k.core.levels import AccessLevel, check_level, parse_level, scopes_for

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RRULE helper — module-level so format.py can import it too
# ---------------------------------------------------------------------------


def rrule_to_human(rrule: str) -> str:
    """Convert an RRULE string to a human-readable summary.

    Handles common patterns: DAILY, WEEKLY, MONTHLY.
    Complex rules fall back to the raw RRULE.
    """
    if not rrule:
        return ""
    parts = dict(p.split("=", 1) for p in rrule.split(";") if "=" in p)
    freq = parts.get("FREQ", "")
    byday = parts.get("BYDAY", "")

    day_map = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu",
               "FR": "Fri", "SA": "Sat", "SU": "Sun"}

    if freq == "DAILY":
        return "daily"
    elif freq == "WEEKLY":
        if byday:
            days = [day_map.get(d.strip(), d.strip()) for d in byday.split(",")]
            return f"weekly on {'+'.join(days)}"
        return "weekly"
    elif freq == "MONTHLY":
        return "monthly"
    elif freq == "YEARLY":
        return "yearly"
    return rrule  # fallback: raw rule


@dataclass
class GcalAdapterConfig:
    """Configuration for a Google Calendar source."""

    email: str
    calendar_id: str
    calendar_name: str
    timezone: str = "UTC"
    config_dir: Path | None = None
    level: str = "readonly"


class GcalAdapter(BaseAdapter):
    """Google Calendar adapter using Calendar API v3."""

    def __init__(self, config: GcalAdapterConfig, prefix: str = "gc") -> None:
        self._config = config
        self._prefix = prefix
        self._access_level = parse_level(config.level)
        self._service: Any | None = None

    # -- BaseAdapter required properties ---------------------------------------

    @property
    def source_prefix(self) -> str:
        return self._prefix

    # access_level is inherited from BaseAdapter (reads self._access_level)

    # -- Lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        from ts4k.auth.google import build_calendar_service

        scopes = scopes_for("gcal", self._access_level)
        self._service = await asyncio.to_thread(
            build_calendar_service,
            email=self._config.email,
            config_dir=self._config.config_dir,
            scopes=scopes,
        )
        logger.info(
            "GcalAdapter connected for %s / %s (level=%s)",
            self._config.email,
            self._config.calendar_name,
            self._access_level.name.lower(),
        )

    async def disconnect(self) -> None:
        if self._service is not None:
            try:
                self._service.close()
            except Exception:
                pass
            self._service = None
            logger.info("GcalAdapter disconnected")

    async def __aenter__(self) -> GcalAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()

    # -- Messaging stubs (return empty for --source all safety) ----------------

    async def whatsnew(self, since: str | None = None,
                       sender: str | None = None,
                       domain: str | None = None) -> list[dict]:
        return []

    async def list_messages(self, query: str | None = None,
                            count: int = 20,
                            page_token: str | None = None,
                            sender: str | None = None,
                            domain: str | None = None) -> list[dict]:
        return []

    async def read_message(self, msg_id: str) -> dict:
        raise NotImplementedError("GcalAdapter does not support read_message")

    async def read_thread(self, thread_id: str) -> dict:
        raise NotImplementedError("GcalAdapter does not support read_thread")

    # -- Level checks ----------------------------------------------------------

    def _check_modify(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.MODIFY, operation, provider="gcal")

    def _check_draft(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.DRAFT, operation, provider="gcal")

    def _check_send(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.SEND, operation, provider="gcal")

    # -- Calendar methods ------------------------------------------------------

    async def list_events(
        self,
        time_min: str,
        time_max: str,
        count: int = 250,
    ) -> list[dict]:
        """Fetch events in a time range, paginated, with normalization."""
        raw_events: list[dict] = []
        page_token: str | None = None

        while len(raw_events) < count:
            result = await asyncio.to_thread(
                lambda pt=page_token: self._service.events().list(
                    calendarId=self._config.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=min(count - len(raw_events), 250),
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=pt,
                ).execute()
            )
            raw_events.extend(result.get("items", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return [self._normalize_event(e) for e in raw_events]

    def _normalize_event(self, event: dict) -> dict:
        """Convert a Google Calendar API event to ts4k normalized dict."""
        # Determine if all-day
        start_raw = event.get("start", {})
        end_raw = event.get("end", {})
        all_day = "date" in start_raw and "dateTime" not in start_raw

        if all_day:
            start = start_raw["date"]
            end = end_raw.get("date", start)
            duration_minutes = None
        else:
            start = start_raw.get("dateTime", "")
            end = end_raw.get("dateTime", "")
            duration_minutes = self._compute_duration(start, end)

        # Attendees
        attendees = event.get("attendees", [])
        your_status = None
        for a in attendees:
            if a.get("self"):
                your_status = a.get("responseStatus")
                break

        # Recurring event ID
        recurring_id = event.get("recurringEventId")

        # Meeting link
        location = event.get("location", "")
        conference = event.get("conferenceData", {})
        meeting_link = ""
        for ep in conference.get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                meeting_link = ep.get("uri", "")
                break

        return {
            "id": f"{self._prefix}:{event['id']}",
            "source": self._prefix,
            "title": event.get("summary", "(No title)"),
            "start": start,
            "end": end,
            "all_day": all_day,
            "duration_minutes": duration_minutes,
            "location": location,
            "organizer": event.get("organizer", {}).get("email", ""),
            "attendees_summary": f"{len(attendees)} people" if attendees else "",
            "status": event.get("status", "confirmed"),
            "your_status": your_status,
            "recurring_event_id": f"{self._prefix}:{recurring_id}" if recurring_id else None,
        }

    @staticmethod
    def _compute_duration(start_iso: str, end_iso: str) -> int | None:
        """Compute duration in minutes between two ISO 8601 datetimes."""
        try:
            s = datetime.fromisoformat(start_iso)
            e = datetime.fromisoformat(end_iso)
            return max(0, int((e - s).total_seconds() / 60))
        except (ValueError, TypeError):
            return None

    async def read_event(self, event_id: str) -> dict:
        """Fetch full detail for a single event via events.get."""
        raw_id = self._strip_prefix(event_id)
        event = await asyncio.to_thread(
            lambda: self._service.events().get(
                calendarId=self._config.calendar_id,
                eventId=raw_id,
            ).execute()
        )
        base = self._normalize_event(event)
        # Add full-detail fields
        attendees_full = []
        for a in event.get("attendees", []):
            attendees_full.append({
                "name": a.get("displayName", a.get("email", "")),
                "email": a.get("email", ""),
                "status": a.get("responseStatus", "needsAction"),
            })
        base["attendees"] = attendees_full
        base["description"] = event.get("description", "")

        # Conference/meeting link
        conference = event.get("conferenceData", {})
        for ep in conference.get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                base["meeting_link"] = ep.get("uri", "")
                break
        else:
            base["meeting_link"] = ""

        # Recurrence — only first RRULE is extracted (multi-rule events are rare;
        # simplification keeps the output compact for LLM consumption)
        recurrence_list = event.get("recurrence", [])
        rrule = ""
        for r in recurrence_list:
            if r.startswith("RRULE:"):
                rrule = r[6:]  # strip RRULE: prefix
                break
        base["recurrence"] = rrule
        base["recurrence_summary"] = rrule_to_human(rrule) if rrule else ""
        base["created"] = event.get("created", "")
        base["updated"] = event.get("updated", "")
        return base

    async def list_calendars(self) -> list[dict]:
        """List available calendars, filtering out freeBusyReader-only."""
        calendars: list[dict] = []
        page_token: str | None = None

        while True:
            result = await asyncio.to_thread(
                lambda pt=page_token: self._service.calendarList().list(
                    pageToken=pt,
                ).execute()
            )
            for cal in result.get("items", []):
                if cal.get("accessRole") == "freeBusyReader":
                    continue
                calendars.append({
                    "id": cal["id"],
                    "summary": cal.get("summary", cal["id"]),
                    "access_role": cal.get("accessRole", "reader"),
                    "timezone": cal.get("timeZone", "UTC"),
                    "primary": cal.get("primary", False),
                })
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return calendars

    # -- Write methods ---------------------------------------------------------

    async def create_event(
        self,
        title: str,
        start: str,
        end: str,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict:
        """Create a calendar event with level-gated attendee support."""
        if attendees:
            self._check_send("create_event")
        else:
            self._check_draft("create_event")

        # Build event body
        body: dict[str, Any] = {"summary": title}
        if description:
            body["description"] = description
        if location:
            body["location"] = location

        # Date handling: detect all-day (no 'T' in string)
        if "T" not in start:
            # All-day: user provides inclusive end, API needs exclusive
            end_date = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
            body["start"] = {"date": start}
            body["end"] = {"date": end_date.strftime("%Y-%m-%d")}
        else:
            body["start"] = {"dateTime": start}
            body["end"] = {"dateTime": end}

        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]

        send_updates = "all" if attendees else "none"
        event = await asyncio.to_thread(
            lambda: self._service.events().insert(
                calendarId=self._config.calendar_id,
                body=body,
                sendUpdates=send_updates,
            ).execute()
        )
        return self._normalize_event(event)

    async def update_event(self, event_id: str, **fields: Any) -> dict:
        """Update an existing event. Requires MODIFY level."""
        self._check_modify("update_event")
        raw_id = self._strip_prefix(event_id)

        body: dict[str, Any] = {}
        if "title" in fields:
            body["summary"] = fields["title"]
        if "description" in fields:
            body["description"] = fields["description"]
        if "location" in fields:
            body["location"] = fields["location"]
        if "start" in fields:
            s = fields["start"]
            body["start"] = {"date": s} if "T" not in s else {"dateTime": s}
        if "end" in fields:
            e = fields["end"]
            body["end"] = {"date": e} if "T" not in e else {"dateTime": e}

        event = await asyncio.to_thread(
            lambda: self._service.events().patch(
                calendarId=self._config.calendar_id,
                eventId=raw_id,
                body=body,
                sendUpdates="all",
            ).execute()
        )
        return self._normalize_event(event)

    async def rsvp(self, event_id: str, status: str) -> dict:
        """RSVP to an event. Requires MODIFY level."""
        self._check_modify("rsvp")
        raw_id = self._strip_prefix(event_id)

        # Fetch current event to find self in attendees
        event = await asyncio.to_thread(
            lambda: self._service.events().get(
                calendarId=self._config.calendar_id,
                eventId=raw_id,
            ).execute()
        )

        attendees = event.get("attendees", [])
        for a in attendees:
            if a.get("self"):
                a["responseStatus"] = status
                break

        updated = await asyncio.to_thread(
            lambda: self._service.events().patch(
                calendarId=self._config.calendar_id,
                eventId=raw_id,
                body={"attendees": attendees},
                sendUpdates="all",
            ).execute()
        )
        return self._normalize_event(updated)

    # -- Helpers ---------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        """Remove the source prefix from an event ID."""
        if prefixed_id.startswith(f"{self._prefix}:"):
            return prefixed_id[len(self._prefix) + 1:]
        return prefixed_id

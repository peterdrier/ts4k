"""Google Calendar adapter — direct Google Calendar API v3."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ts4k.adapters.base import BaseAdapter
from ts4k.core.levels import AccessLevel, check_level, parse_level, scopes_for

logger = logging.getLogger(__name__)


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

    # -- Helpers ---------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        """Remove the source prefix from an event ID."""
        if prefixed_id.startswith(f"{self._prefix}:"):
            return prefixed_id[len(self._prefix) + 1:]
        return prefixed_id

"""CalDAV calendar adapter — generic RFC 4791, Apple/iCloud preset.

Wraps the synchronous ``caldav`` library in ``asyncio.to_thread`` (same
wrap-a-sync-client pattern as the Google adapter).  Auth is HTTP Basic
with an app-specific password loaded from
``~/.config/ts4k/caldav/<email>/credentials.json``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ts4k.adapters.base import BaseAdapter
from ts4k.auth.caldav import load_credentials
from ts4k.core.levels import AccessLevel, check_level, parse_level

logger = logging.getLogger(__name__)

_PARTSTAT_MAP = {
    "ACCEPTED": "accepted",
    "DECLINED": "declined",
    "TENTATIVE": "tentative",
    "NEEDS-ACTION": "needsAction",
    "DELEGATED": "delegated",
}


def _strip_mailto(value: Any) -> str:
    s = str(value)
    return s[7:] if s.lower().startswith("mailto:") else s


@dataclass
class CaldavAdapterConfig:
    """Configuration for a CalDAV calendar source."""

    email: str            # account identity (Apple ID)
    server_url: str       # e.g. https://caldav.icloud.com
    calendar_id: str      # CalDAV calendar URL
    calendar_name: str = ""
    timezone: str = "UTC"
    config_dir: Path | None = None
    level: str = "readonly"


class CaldavAdapter(BaseAdapter):
    """Generic CalDAV calendar adapter (iCloud, Fastmail, Nextcloud, ...)."""

    def __init__(self, config: CaldavAdapterConfig, prefix: str = "cc") -> None:
        self._config = config
        self._prefix = prefix
        self._access_level = parse_level(config.level)
        self._client: Any = None
        self._principal: Any = None
        self._calendar: Any = None

    @property
    def source_prefix(self) -> str:
        return self._prefix

    # -- Connection ------------------------------------------------------------

    async def connect(self) -> None:
        import caldav
        from caldav.lib.error import AuthorizationError

        email = self._config.email
        creds = load_credentials(email, self._config.config_dir)
        if creds is None:
            raise RuntimeError(
                f"No CalDAV credentials for {email} — an app-specific password is "
                f"required (generate at https://account.apple.com, then run: "
                f"ts4k src add <prefix> apple email={email})"
            )

        def _connect() -> tuple[Any, Any]:
            client = caldav.DAVClient(
                url=creds.get("server_url") or self._config.server_url,
                username=creds["username"],
                password=creds["app_password"],
            )
            return client, client.principal()

        try:
            self._client, self._principal = await asyncio.to_thread(_connect)
        except AuthorizationError as e:
            raise RuntimeError(
                f"CalDAV auth failed for {email} — the app-specific password may be "
                f"revoked (they expire when the Apple ID password changes). Generate "
                f"a new one at https://account.apple.com, delete "
                f"~/.config/ts4k/caldav/{email}/credentials.json, and re-run "
                f"ts4k src add."
            ) from e

    async def disconnect(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
        self._client = None
        self._principal = None
        self._calendar = None

    async def __aenter__(self) -> CaldavAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()

    async def _get_calendar(self) -> Any:
        """Resolve and cache the configured calendar by URL."""
        if self._calendar is None:
            wanted = self._config.calendar_id.rstrip("/")

            def _find() -> Any:
                for c in self._principal.calendars():
                    if str(c.url).rstrip("/") == wanted:
                        return c
                raise RuntimeError(
                    f"Calendar {self._config.calendar_id!r} not found for "
                    f"{self._config.email}"
                )

            self._calendar = await asyncio.to_thread(_find)
        return self._calendar

    # -- Calendar methods ------------------------------------------------------

    async def list_events(
        self,
        time_min: str,
        time_max: str,
        count: int = 250,
    ) -> list[dict]:
        """Fetch events in a time range, expanded to instances, sorted by start."""
        cal = await self._get_calendar()
        start = datetime.fromisoformat(time_min)
        end = datetime.fromisoformat(time_max)
        results = await asyncio.to_thread(
            lambda: cal.search(start=start, end=end, event=True, expand=True)
        )
        events = [self._normalize_component(r.icalendar_component) for r in results]
        events.sort(key=lambda e: e.get("start", ""))
        return events[:count]

    def _normalize_component(self, comp: Any) -> dict:
        """Convert an icalendar VEVENT to the ts4k normalized event dict.

        Same keys as GcalAdapter._normalize_event so format.py needs no changes.
        """
        uid = str(comp.get("UID", ""))
        tzinfo = self._tzinfo()

        dtstart = comp.get("DTSTART")
        start_dt = dtstart.dt if dtstart is not None else None
        all_day = isinstance(start_dt, date) and not isinstance(start_dt, datetime)

        dtend = comp.get("DTEND")
        if dtend is not None:
            end_dt = dtend.dt
        elif comp.get("DURATION") is not None and start_dt is not None:
            end_dt = start_dt + comp.get("DURATION").dt
        else:
            end_dt = start_dt

        if isinstance(start_dt, datetime) and start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=tzinfo)
        if isinstance(end_dt, datetime) and end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=tzinfo)
        if isinstance(start_dt, datetime):
            start_dt = start_dt.astimezone(tzinfo)
        if isinstance(end_dt, datetime):
            end_dt = end_dt.astimezone(tzinfo)

        start = start_dt.isoformat() if start_dt is not None else ""
        end = end_dt.isoformat() if end_dt is not None else start
        if all_day:
            duration_minutes = None
        elif start_dt is not None and end_dt is not None:
            duration_minutes = max(0, int((end_dt - start_dt).total_seconds() / 60))
        else:
            duration_minutes = None

        raw_attendees = comp.get("ATTENDEE")
        if raw_attendees is None:
            attendees = []
        elif isinstance(raw_attendees, list):
            attendees = raw_attendees
        else:
            attendees = [raw_attendees]

        your_status = None
        my_email = self._config.email.lower()
        for a in attendees:
            if _strip_mailto(a).lower() == my_email:
                partstat = str(a.params.get("PARTSTAT", "NEEDS-ACTION")).upper()
                your_status = _PARTSTAT_MAP.get(partstat, partstat.lower())
                break

        organizer_prop = comp.get("ORGANIZER")
        organizer = _strip_mailto(organizer_prop) if organizer_prop is not None else ""

        recurrence_id = comp.get("RECURRENCE-ID")
        if recurrence_id is not None:
            rid = recurrence_id.dt
            if isinstance(rid, datetime) and rid.tzinfo is None:
                rid = rid.replace(tzinfo=tzinfo)
            if isinstance(rid, datetime):
                rid = rid.astimezone(tzinfo)
            event_id = f"{uid}::{rid.isoformat()}"
            recurring_event_id = f"{self._prefix}:{uid}"
        else:
            event_id = uid
            recurring_event_id = None

        summary = comp.get("SUMMARY")
        status_prop = comp.get("STATUS")
        location_prop = comp.get("LOCATION")

        return {
            "id": f"{self._prefix}:{event_id}",
            "source": self._prefix,
            "title": str(summary) if summary else "(No title)",
            "start": start,
            "end": end,
            "all_day": all_day,
            "duration_minutes": duration_minutes,
            "location": str(location_prop) if location_prop else "",
            "organizer": organizer,
            "attendees_summary": f"{len(attendees)} people" if attendees else "",
            "status": str(status_prop).lower() if status_prop else "confirmed",
            "your_status": your_status,
            "recurring_event_id": recurring_event_id,
        }

    def _tzinfo(self) -> ZoneInfo | timezone:
        try:
            return ZoneInfo(self._config.timezone)
        except Exception:
            return timezone.utc

    async def _fetch_by_uid(self, uid: str) -> Any:
        cal = await self._get_calendar()
        return await asyncio.to_thread(lambda: cal.event_by_uid(uid))

    async def read_event(self, event_id: str) -> dict:
        """Fetch full detail for a single event by UID.

        Instance IDs (``uid::<recurrence-id>``) resolve to the series master.
        """
        raw = self._strip_prefix(event_id)
        uid = raw.split("::")[0]
        obj = await self._fetch_by_uid(uid)
        comp = obj.icalendar_component
        base = self._normalize_component(comp)

        attendees_full = []
        raw_attendees = comp.get("ATTENDEE")
        if raw_attendees is None:
            raw_attendees = []
        elif not isinstance(raw_attendees, list):
            raw_attendees = [raw_attendees]
        for a in raw_attendees:
            email = _strip_mailto(a)
            partstat = str(a.params.get("PARTSTAT", "NEEDS-ACTION")).upper()
            attendees_full.append({
                "name": str(a.params.get("CN", email)),
                "email": email,
                "status": _PARTSTAT_MAP.get(partstat, partstat.lower()),
            })
        base["attendees"] = attendees_full

        desc = comp.get("DESCRIPTION")
        base["description"] = str(desc) if desc else ""
        url = comp.get("URL")
        base["meeting_link"] = str(url) if url else ""

        from ts4k.adapters.gcal import rrule_to_human

        rrule_prop = comp.get("RRULE")
        rrule = rrule_prop.to_ical().decode() if rrule_prop is not None else ""
        base["recurrence"] = rrule
        base["recurrence_summary"] = rrule_to_human(rrule) if rrule else ""

        created = comp.get("CREATED")
        base["created"] = created.dt.isoformat() if created is not None else ""
        updated = comp.get("LAST-MODIFIED")
        base["updated"] = updated.dt.isoformat() if updated is not None else ""
        return base

    async def list_calendars(self) -> list[dict]:
        """List calendars on the principal (used by setup; adapter may have empty calendar_id)."""

        def _list() -> list[dict]:
            out = []
            for c in self._principal.calendars():
                out.append({
                    "id": str(c.url),
                    "summary": c.name or str(c.url),
                    "access_role": "owner",
                    "timezone": self._config.timezone,
                    "primary": False,
                })
            return out

        return await asyncio.to_thread(_list)

    # -- Messaging stubs (calendar sources have no messages) -------------------

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
        raise NotImplementedError("CaldavAdapter does not support read_message")

    async def read_thread(self, thread_id: str) -> dict:
        raise NotImplementedError("CaldavAdapter does not support read_thread")

    # -- Level checks ----------------------------------------------------------

    def _check_modify(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.MODIFY, operation, provider="caldav")

    def _check_draft(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.DRAFT, operation, provider="caldav")

    def _check_send(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.SEND, operation, provider="caldav")

    # -- Helpers ---------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        if prefixed_id.startswith(f"{self._prefix}:"):
            return prefixed_id[len(self._prefix) + 1:]
        return prefixed_id

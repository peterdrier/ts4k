"""O365 Calendar adapter — Microsoft Graph API via httpx."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ts4k.adapters.base import BaseAdapter
from ts4k.core.levels import AccessLevel, check_level, parse_level, scopes_for
from ts4k.core.tz import to_utc_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph recurrence -> human string
# ---------------------------------------------------------------------------

_DAY_MAP = {
    "sunday": "Sun", "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed",
    "thursday": "Thu", "friday": "Fri", "saturday": "Sat",
}


def graph_recurrence_to_human(recurrence: dict | None) -> str:
    """Convert a Graph API recurrence object to a human-readable string."""
    if not recurrence:
        return ""
    pattern = recurrence.get("pattern", {})
    rtype = pattern.get("type", "")
    interval = pattern.get("interval", 1)
    days_of_week = pattern.get("daysOfWeek", [])

    prefix = ""
    if interval > 1:
        prefix = f"every {interval} "

    if rtype == "daily":
        return f"{prefix}daily" if prefix else "daily"
    elif rtype == "weekly":
        if days_of_week:
            days = [_DAY_MAP.get(d.lower(), d) for d in days_of_week]
            return f"{prefix}weekly on {'+'.join(days)}" if prefix else f"weekly on {'+'.join(days)}"
        return f"{prefix}weekly" if prefix else "weekly"
    elif rtype in ("absoluteMonthly", "relativeMonthly"):
        return f"{prefix}monthly" if prefix else "monthly"
    elif rtype in ("absoluteYearly", "relativeYearly"):
        return f"{prefix}yearly" if prefix else "yearly"
    return "recurring"


@dataclass
class O365CalAdapterConfig:
    """Configuration for an O365 Calendar source."""

    email: str
    client_id: str
    tenant_id: str = "common"
    calendar_id: str = "default"
    calendar_name: str = ""
    timezone: str = "UTC"
    config_dir: Path | None = None
    level: str = "readonly"


class O365CalAdapter(BaseAdapter):
    """O365 Calendar adapter using Microsoft Graph API via httpx."""

    def __init__(self, config: O365CalAdapterConfig, prefix: str = "oc") -> None:
        self._config = config
        self._prefix = prefix
        self._access_level = parse_level(config.level)
        self._client: httpx.AsyncClient | None = None

    @property
    def source_prefix(self) -> str:
        return self._prefix

    # -- Lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        if self._client is not None:
            return

        from ts4k.auth.microsoft import build_graph_client

        scopes = scopes_for("o365cal", self._access_level)
        self._client = await asyncio.to_thread(
            build_graph_client,
            self._config.client_id,
            tenant_id=self._config.tenant_id,
            config_dir=self._config.config_dir,
            scopes=scopes,
            username=self._config.email,
        )
        logger.info(
            "O365CalAdapter connected for %s / %s (level=%s)",
            self._config.email,
            self._config.calendar_name,
            self._access_level.name.lower(),
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("O365CalAdapter disconnected")

    async def __aenter__(self) -> O365CalAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "O365CalAdapter is not connected. Call connect() or use "
                "'async with adapter:' first."
            )
        return self._client

    # -- HTTP helpers ------------------------------------------------------

    async def _get(self, path: str, params: dict[str, str] | None = None,
                   headers: dict[str, str] | None = None) -> dict:
        client = self._require_client()
        resp = await client.get(path, params=params or {}, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json: dict | None = None) -> httpx.Response:
        """POST returning the raw Response (some endpoints return empty body)."""
        client = self._require_client()
        resp = await client.post(path, json=json or {})
        resp.raise_for_status()
        return resp

    async def _patch(self, path: str, json: dict) -> dict:
        client = self._require_client()
        resp = await client.patch(path, json=json)
        resp.raise_for_status()
        return resp.json()

    # -- Messaging stubs ---------------------------------------------------

    async def whatsnew(self, since: str | None = None,
                       sender: str | None = None,
                       domain: str | None = None,
                       count: int = 200) -> list[dict]:
        return []

    async def list_messages(self, query: str | None = None,
                            count: int = 20,
                            page_token: str | None = None,
                            sender: str | None = None,
                            domain: str | None = None) -> list[dict]:
        return []

    async def read_message(self, msg_id: str, prefer_html: bool = False) -> dict:
        raise NotImplementedError("O365CalAdapter does not support read_message")

    async def read_thread(self, thread_id: str) -> dict:
        raise NotImplementedError("O365CalAdapter does not support read_thread")

    # -- Level checks ------------------------------------------------------

    def _check_modify(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.MODIFY, operation, provider="o365cal")

    def _check_draft(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.DRAFT, operation, provider="o365cal")

    def _check_send(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.SEND, operation, provider="o365cal")

    # -- Calendar path helper ----------------------------------------------

    def _calendar_path(self) -> str:
        cal_id = self._config.calendar_id
        if cal_id in ("default", "primary", ""):
            return "/me"
        return f"/me/calendars/{cal_id}"

    # -- Calendar methods --------------------------------------------------

    async def list_events(
        self,
        time_min: str,
        time_max: str,
        count: int = 250,
    ) -> list[dict]:
        """Fetch events in a time range via calendarView, with pagination."""
        raw_events: list[dict] = []
        next_link: str | None = None

        params = {
            "startDateTime": time_min,
            "endDateTime": time_max,
            "$top": str(min(count, 250)),
            "$orderby": "start/dateTime",
        }

        # Pin the response zone. Graph is otherwise free to answer in the
        # mailbox's own zone, whose IDs are Windows-style ("Eastern Standard
        # Time") — names ZoneInfo cannot resolve, which would silently
        # degrade every event to UTC and shift its displayed time.
        headers = {"Prefer": 'outlook.timezone="UTC"'}

        while len(raw_events) < count:
            if next_link:
                client = self._require_client()
                resp = await client.get(next_link, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            else:
                data = await self._get(
                    f"{self._calendar_path()}/calendarView", params, headers=headers,
                )

            raw_events.extend(data.get("value", []))
            next_link = data.get("@odata.nextLink")
            if not next_link:
                break

        return [self._normalize_event(e) for e in raw_events[:count]]

    def _normalize_event(self, event: dict) -> dict:
        """Convert a Graph API event to ts4k normalized dict."""
        all_day = event.get("isAllDay", False)

        start_raw = event.get("start", {})
        end_raw = event.get("end", {})

        if all_day:
            # All-day events are dates, not instants — never converted.
            start = start_raw.get("dateTime", "").split("T")[0]
            end = end_raw.get("dateTime", "").split("T")[0]
            duration_minutes = None
        else:
            # Graph puts the zone in a sibling field, not in the dateTime
            # string; stored in UTC, rendered by the format layer.
            start = to_utc_iso(start_raw.get("dateTime", ""), start_raw.get("timeZone", "UTC"))
            end = to_utc_iso(end_raw.get("dateTime", ""), end_raw.get("timeZone", "UTC"))
            duration_minutes = self._compute_duration(start, end)

        your_status_raw = event.get("responseStatus", {}).get("response", "")
        your_status = your_status_raw if your_status_raw not in ("none", "organizer", "") else None

        attendees = event.get("attendees", [])
        series_master_id = event.get("seriesMasterId")

        return {
            "id": f"{self._prefix}:{event['id']}",
            "source": self._prefix,
            "title": event.get("subject", "(No title)"),
            "start": start,
            "end": end,
            "all_day": all_day,
            "duration_minutes": duration_minutes,
            "location": event.get("location", {}).get("displayName", ""),
            "organizer": event.get("organizer", {}).get("emailAddress", {}).get("address", ""),
            "attendees_summary": f"{len(attendees)} people" if attendees else "",
            "status": "cancelled" if event.get("isCancelled") else "confirmed",
            "your_status": your_status,
            "recurring_event_id": f"{self._prefix}:{series_master_id}" if series_master_id else None,
        }

    @staticmethod
    def _compute_duration(start_iso: str, end_iso: str) -> int | None:
        try:
            s = datetime.fromisoformat(start_iso)
            e = datetime.fromisoformat(end_iso)
            return max(0, int((e - s).total_seconds() / 60))
        except (ValueError, TypeError):
            return None

    async def read_event(self, event_id: str) -> dict:
        """Fetch full detail for a single event."""
        raw_id = self._strip_prefix(event_id)
        event = await self._get(f"/me/events/{raw_id}")

        base = self._normalize_event(event)

        attendees_full = []
        for a in event.get("attendees", []):
            ea = a.get("emailAddress", {})
            attendees_full.append({
                "name": ea.get("name", ea.get("address", "")),
                "email": ea.get("address", ""),
                "status": a.get("status", {}).get("response", "none"),
            })
        base["attendees"] = attendees_full

        body = event.get("body", {})
        base["description"] = body.get("content", "") if isinstance(body, dict) else ""

        online = event.get("onlineMeeting") or {}
        base["meeting_link"] = online.get("joinUrl", "")

        recurrence = event.get("recurrence")
        base["recurrence"] = recurrence
        base["recurrence_summary"] = graph_recurrence_to_human(recurrence)

        base["created"] = event.get("createdDateTime", "")
        base["updated"] = event.get("lastModifiedDateTime", "")
        return base

    async def list_calendars(self) -> list[dict]:
        """List available calendars for the authenticated user."""
        # Fetch user's timezone from mailbox settings
        user_tz = "UTC"
        try:
            settings = await self._get("/me/mailboxSettings")
            user_tz = settings.get("timeZone", "UTC")
        except Exception:
            pass  # Fall back to UTC if mailbox settings unavailable

        calendars: list[dict] = []
        next_link: str | None = None

        while True:
            if next_link:
                client = self._require_client()
                resp = await client.get(next_link)
                resp.raise_for_status()
                data = resp.json()
            else:
                data = await self._get("/me/calendars")

            for cal in data.get("value", []):
                calendars.append({
                    "id": cal["id"],
                    "summary": cal.get("name", cal["id"]),
                    "access_role": "owner" if cal.get("canEdit") else "reader",
                    "primary": cal.get("isDefaultCalendar", False),
                    "timezone": user_tz,
                })

            next_link = data.get("@odata.nextLink")
            if not next_link:
                break

        return calendars

    # -- Write methods -----------------------------------------------------

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

        body: dict[str, Any] = {"subject": title}
        if description:
            body["body"] = {"contentType": "text", "content": description}
        if location:
            body["location"] = {"displayName": location}

        # Date handling: detect all-day (no 'T' in string)
        if "T" not in start:
            from datetime import timedelta
            end_date = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
            body["start"] = {"dateTime": start, "timeZone": self._config.timezone}
            body["end"] = {"dateTime": end_date.strftime("%Y-%m-%d"), "timeZone": self._config.timezone}
            body["isAllDay"] = True
        else:
            body["start"] = {"dateTime": start, "timeZone": self._config.timezone}
            body["end"] = {"dateTime": end, "timeZone": self._config.timezone}

        if attendees:
            body["attendees"] = [
                {"emailAddress": {"address": e}, "type": "required"}
                for e in attendees
            ]

        cal_path = self._calendar_path()
        events_path = f"{cal_path}/events" if cal_path != "/me" else "/me/events"
        resp = await self._post(events_path, json=body)
        event = resp.json()
        return self._normalize_event(event)

    async def update_event(self, event_id: str, **fields: Any) -> dict:
        """Update an existing event. Requires MODIFY level."""
        self._check_modify("update_event")
        raw_id = self._strip_prefix(event_id)

        body: dict[str, Any] = {}
        if "title" in fields:
            body["subject"] = fields["title"]
        if "description" in fields:
            body["body"] = {"contentType": "text", "content": fields["description"]}
        if "location" in fields:
            body["location"] = {"displayName": fields["location"]}
        if "start" in fields:
            s = fields["start"]
            body["start"] = {"dateTime": s, "timeZone": self._config.timezone}
            if "T" not in s:
                body["isAllDay"] = True
        if "end" in fields:
            e = fields["end"]
            if "T" not in e:
                # All-day: user provides inclusive end, Graph end is exclusive
                from datetime import timedelta
                end_date = datetime.strptime(e, "%Y-%m-%d") + timedelta(days=1)
                body["end"] = {"dateTime": end_date.strftime("%Y-%m-%d"),
                               "timeZone": self._config.timezone}
                body["isAllDay"] = True
            else:
                body["end"] = {"dateTime": e, "timeZone": self._config.timezone}

        event = await self._patch(f"/me/events/{raw_id}", json=body)
        return self._normalize_event(event)

    _RSVP_ENDPOINTS = {
        "accepted": "accept",
        "declined": "decline",
        "tentative": "tentativelyAccept",
    }

    async def rsvp(self, event_id: str, status: str) -> dict:
        """RSVP to an event. Requires MODIFY level."""
        self._check_modify("rsvp")
        raw_id = self._strip_prefix(event_id)

        endpoint = self._RSVP_ENDPOINTS.get(status)
        if not endpoint:
            raise ValueError(f"Invalid RSVP status: {status!r}")

        # POST returns 202 with empty body — don't parse JSON
        await self._post(f"/me/events/{raw_id}/{endpoint}", json={"sendResponse": True})

        # Re-fetch to return updated normalized event
        return await self.read_event(event_id)

    # -- Helpers -----------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        if prefixed_id.startswith(f"{self._prefix}:"):
            return prefixed_id[len(self._prefix) + 1:]
        return prefixed_id

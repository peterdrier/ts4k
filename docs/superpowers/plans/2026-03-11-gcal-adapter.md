# Google Calendar Adapter (Phase 6a) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Google Calendar adapter to ts4k with `ts4k cal` CLI commands, MCP tools, and read/write operations gated by access levels.

**Architecture:** New `GcalAdapter` follows the existing adapter pattern (config dataclass + BaseAdapter subclass + asyncio.to_thread for sync Google API). Calendar commands are a separate command family in `commands.py` — they call `GcalAdapter` directly, not through `_fetch_messages`. Recurring events are collapsed in the formatter for multi-week views.

**Tech Stack:** Google Calendar API v3 via `google-api-python-client` (already a dependency), same OAuth flow as Gmail.

**Spec:** `docs/superpowers/specs/2026-03-11-gcal-adapter-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/ts4k/adapters/gcal.py` | GcalAdapterConfig, GcalAdapter (connect, list_events, read_event, list_calendars, create_event, update_event, rsvp, messaging stubs) |
| `tests/test_gcal_adapter.py` | Adapter unit tests: list_events, read_event, list_calendars, pagination, normalization, timezone, all-day events |
| `tests/test_gcal_write.py` | Adapter write tests: create_event level gating, all-day date conversion, update_event, rsvp |
| `tests/test_cal_format.py` | Event formatting: pipe/json/xml, adaptive time, recurring collapsing, RRULE summary, all-day display |
| `tests/test_cal_commands.py` | Command layer: cal_today, cal_week, cal_next, cal_range, cal_event, multi-source merge |

### Modified Files

| File | Changes |
|------|---------|
| `src/ts4k/core/levels.py` | Add `_GCAL_SCOPES`, make `check_level` provider-aware, add `"gcal"` to `scopes_for` |
| `src/ts4k/auth/google.py` | Add `build_calendar_service()` |
| `src/ts4k/core/format.py` | Add `format_events()`, `format_event_detail()`, RRULE-to-human helper, recurring collapsing |
| `src/ts4k/commands.py` | Add gcal import, `_make_adapter` branch, provider aliases, all `cal_*` command functions |
| `src/ts4k/cli.py` | Add `cal` subparser with subcommands, setup wizard interactive logic, auth command gcal awareness |
| `src/ts4k/server.py` | Add `cal`, `cal_create`, `cal_manage` MCP tools |

---

## Chunk 1: Foundation — Levels + Auth

### Task 1: Add gcal scopes to levels.py

**Files:**
- Modify: `src/ts4k/core/levels.py:84-94`
- Test: `tests/test_levels.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_levels.py`:

```python
def test_scopes_for_gcal_readonly():
    from ts4k.core.levels import AccessLevel, scopes_for
    scopes = scopes_for("gcal", AccessLevel.READONLY)
    assert scopes == ["https://www.googleapis.com/auth/calendar.readonly"]


def test_scopes_for_gcal_modify():
    from ts4k.core.levels import AccessLevel, scopes_for
    scopes = scopes_for("gcal", AccessLevel.MODIFY)
    assert scopes == ["https://www.googleapis.com/auth/calendar"]


def test_scopes_for_gcal_send():
    from ts4k.core.levels import AccessLevel, scopes_for
    scopes = scopes_for("gcal", AccessLevel.SEND)
    assert scopes == ["https://www.googleapis.com/auth/calendar"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_levels.py::test_scopes_for_gcal_readonly tests/test_levels.py::test_scopes_for_gcal_modify tests/test_levels.py::test_scopes_for_gcal_send -v`
Expected: FAIL — `scopes_for("gcal", ...)` returns `[]`

- [ ] **Step 3: Add _GCAL_SCOPES and update scopes_for**

In `src/ts4k/core/levels.py`, after `_O365_SCOPES` (line 84), add:

```python
_GCAL_SCOPES: dict[AccessLevel, list[str]] = {
    AccessLevel.READONLY: ["https://www.googleapis.com/auth/calendar.readonly"],
    AccessLevel.MODIFY: ["https://www.googleapis.com/auth/calendar"],
    AccessLevel.DRAFT: ["https://www.googleapis.com/auth/calendar"],
    AccessLevel.SEND: ["https://www.googleapis.com/auth/calendar"],
}
```

In `scopes_for()` (line 87-94), add before the final `return []`:

```python
    if provider == "gcal":
        return list(_GCAL_SCOPES.get(level, []))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_levels.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/core/levels.py tests/test_levels.py
git commit -m "feat: add gcal OAuth scope mapping to levels.py"
```

---

### Task 2: Make check_level provider-aware for SEND

**Files:**
- Modify: `src/ts4k/core/levels.py:38-50`
- Test: `tests/test_levels.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_levels.py`:

```python
def test_check_level_send_blocked_for_messaging():
    """SEND is blocked for non-gcal providers (the default)."""
    from ts4k.core.levels import AccessLevel, check_level
    with pytest.raises(NotImplementedError, match="never sends messages"):
        check_level(AccessLevel.SEND, AccessLevel.SEND, "send_message")


def test_check_level_send_allowed_for_gcal():
    """SEND is allowed when provider='gcal'."""
    from ts4k.core.levels import AccessLevel, check_level
    # Should NOT raise — gcal SEND is permitted
    check_level(AccessLevel.SEND, AccessLevel.SEND, "create_event", provider="gcal")


def test_check_level_send_blocked_for_gmail():
    """SEND is still blocked for gmail even with explicit provider."""
    from ts4k.core.levels import AccessLevel, check_level
    with pytest.raises(NotImplementedError):
        check_level(AccessLevel.SEND, AccessLevel.SEND, "send", provider="gmail")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_levels.py::test_check_level_send_allowed_for_gcal -v`
Expected: FAIL — `NotImplementedError` raised because current check_level has no `provider` param

- [ ] **Step 3: Update check_level signature**

In `src/ts4k/core/levels.py`, replace `check_level` (lines 38-50) with:

```python
def check_level(current: AccessLevel, required: AccessLevel, operation: str,
                *, provider: str | None = None) -> None:
    """Raise PermissionError if current level is below required.

    SEND level is blocked for messaging providers (ts4k never sends messages)
    but permitted for calendar providers (invites are a normal workflow).
    """
    if required >= AccessLevel.SEND:
        if provider != "gcal":
            raise NotImplementedError(
                f"Operation '{operation}' requires level 'send', which is "
                "intentionally not implemented for messaging. "
                "ts4k never sends messages."
            )
    if current < required:
        raise PermissionError(
            f"Operation '{operation}' requires level='{required.name.lower()}', "
            f"but source is configured as level='{current.name.lower()}'. "
            f"Update with: ts4k src add <prefix> <provider> level={required.name.lower()}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_levels.py -v`
Expected: ALL PASS (existing tests still pass because `provider=None` triggers the messaging block)

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/core/levels.py tests/test_levels.py
git commit -m "feat: make check_level provider-aware, allow SEND for gcal"
```

---

### Task 3: Add build_calendar_service to auth/google.py

**Files:**
- Modify: `src/ts4k/auth/google.py:160-170`
- Test: `tests/test_google_auth.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_google_auth.py`:

```python
def test_build_calendar_service(monkeypatch):
    """build_calendar_service calls get_credentials and builds calendar v3."""
    from unittest.mock import MagicMock, patch
    from ts4k.auth.google import build_calendar_service

    mock_creds = MagicMock()
    mock_service = MagicMock()

    with patch("ts4k.auth.google.get_credentials", return_value=mock_creds) as mock_get, \
         patch("ts4k.auth.google.build", return_value=mock_service) as mock_build:
        result = build_calendar_service("test@gmail.com", scopes=["calendar.readonly"])

    mock_get.assert_called_once_with("test@gmail.com", ["calendar.readonly"], None)
    mock_build.assert_called_once_with("calendar", "v3", credentials=mock_creds)
    assert result is mock_service
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_google_auth.py::test_build_calendar_service -v`
Expected: FAIL — `ImportError: cannot import name 'build_calendar_service'`

- [ ] **Step 3: Add build_calendar_service**

In `src/ts4k/auth/google.py`, after `build_gmail_service` (line ~170), add:

```python
def build_calendar_service(
    email: str,
    config_dir: Path | None = None,
    scopes: list[str] | None = None,
):
    """Build a Google Calendar API v3 service client.

    Reuses the same credential flow as Gmail — tokens are shared per email.
    If the account already has a Gmail token, adding calendar scopes triggers
    a one-time re-auth.
    """
    creds = get_credentials(email, scopes or [], config_dir)
    return build("calendar", "v3", credentials=creds)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_google_auth.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/auth/google.py tests/test_google_auth.py
git commit -m "feat: add build_calendar_service to auth/google.py"
```

---

## Chunk 2: Adapter — GcalAdapter Core

### Task 4: Create GcalAdapter with config, connect, disconnect, messaging stubs

**Files:**
- Create: `src/ts4k/adapters/gcal.py`
- Create: `tests/test_gcal_adapter.py`

- [ ] **Step 1: Write the failing test for adapter construction and messaging stubs**

Create `tests/test_gcal_adapter.py`:

```python
"""Tests for the Google Calendar adapter."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from ts4k.adapters.gcal import GcalAdapter, GcalAdapterConfig
from ts4k.core.levels import AccessLevel


@pytest.fixture
def gcal_config(tmp_path: Path) -> GcalAdapterConfig:
    return GcalAdapterConfig(
        email="test@gmail.com",
        calendar_id="primary",
        calendar_name="Test Calendar",
        timezone="Europe/Amsterdam",
        config_dir=tmp_path,
        level="readonly",
    )


@pytest.fixture
def adapter(gcal_config: GcalAdapterConfig) -> GcalAdapter:
    return GcalAdapter(gcal_config, prefix="gc")


class TestConstruction:
    def test_prefix(self, adapter: GcalAdapter):
        assert adapter.source_prefix == "gc"

    def test_access_level(self, adapter: GcalAdapter):
        assert adapter.access_level == AccessLevel.READONLY


class TestMessagingStubs:
    """Messaging methods return empty results (not raise) for --source all safety."""

    @pytest.mark.asyncio
    async def test_whatsnew_returns_empty(self, adapter: GcalAdapter):
        result = await adapter.whatsnew(since="2026-01-01")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_messages_returns_empty(self, adapter: GcalAdapter):
        result = await adapter.list_messages()
        assert result == []

    @pytest.mark.asyncio
    async def test_read_message_raises(self, adapter: GcalAdapter):
        with pytest.raises(NotImplementedError):
            await adapter.read_message("gc:123")

    @pytest.mark.asyncio
    async def test_read_thread_raises(self, adapter: GcalAdapter):
        with pytest.raises(NotImplementedError):
            await adapter.read_thread("gc:t123")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gcal_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ts4k.adapters.gcal'`

- [ ] **Step 3: Create the adapter file with config, lifecycle, and stubs**

Create `src/ts4k/adapters/gcal.py`:

```python
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

    # -- Helpers ---------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        """Remove the source prefix from an event ID."""
        if prefixed_id.startswith(f"{self._prefix}:"):
            return prefixed_id[len(self._prefix) + 1:]
        return prefixed_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gcal_adapter.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/adapters/gcal.py tests/test_gcal_adapter.py
git commit -m "feat: create GcalAdapter skeleton with config, lifecycle, messaging stubs"
```

---

### Task 5: Add list_events with pagination and normalization

**Files:**
- Modify: `src/ts4k/adapters/gcal.py`
- Modify: `tests/test_gcal_adapter.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gcal_adapter.py`:

```python
class TestListEvents:
    """Test list_events: API call, pagination, normalization."""

    @pytest.mark.asyncio
    async def test_basic_timed_event(self, adapter: GcalAdapter):
        """Timed event is normalized correctly."""
        api_event = {
            "id": "evt1",
            "summary": "Standup",
            "start": {"dateTime": "2026-03-11T09:00:00+01:00"},
            "end": {"dateTime": "2026-03-11T09:30:00+01:00"},
            "status": "confirmed",
            "organizer": {"email": "sarah@work.com"},
            "attendees": [
                {"email": "sarah@work.com", "responseStatus": "accepted"},
                {"email": "test@gmail.com", "responseStatus": "accepted", "self": True},
                {"email": "mike@work.com", "responseStatus": "tentative"},
            ],
        }
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [api_event],
        }
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00+01:00",
            time_max="2026-03-12T00:00:00+01:00",
        )

        assert len(result) == 1
        evt = result[0]
        assert evt["id"] == "gc:evt1"
        assert evt["title"] == "Standup"
        assert evt["all_day"] is False
        assert evt["duration_minutes"] == 30
        assert evt["organizer"] == "sarah@work.com"
        assert evt["attendees_summary"] == "3 people"
        assert evt["your_status"] == "accepted"
        assert evt["recurring_event_id"] is None

    @pytest.mark.asyncio
    async def test_all_day_event(self, adapter: GcalAdapter):
        """All-day event uses date keys, not dateTime."""
        api_event = {
            "id": "evt2",
            "summary": "Vacation",
            "start": {"date": "2026-03-17"},
            "end": {"date": "2026-03-22"},  # exclusive: Mon-Fri = Sat end
            "status": "confirmed",
        }
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [api_event],
        }
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-17T00:00:00+01:00",
            time_max="2026-03-23T00:00:00+01:00",
        )

        evt = result[0]
        assert evt["all_day"] is True
        assert evt["start"] == "2026-03-17"
        assert evt["end"] == "2026-03-22"  # raw API value (exclusive)

    @pytest.mark.asyncio
    async def test_recurring_event_has_recurring_id(self, adapter: GcalAdapter):
        """Expanded recurring instance includes recurringEventId."""
        api_event = {
            "id": "evt1_20260311",
            "recurringEventId": "evt1",
            "summary": "Standup",
            "start": {"dateTime": "2026-03-11T09:00:00+01:00"},
            "end": {"dateTime": "2026-03-11T09:30:00+01:00"},
            "status": "confirmed",
        }
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [api_event],
        }
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00+01:00",
            time_max="2026-03-12T00:00:00+01:00",
        )

        assert result[0]["recurring_event_id"] == "gc:evt1"

    @pytest.mark.asyncio
    async def test_pagination(self, adapter: GcalAdapter):
        """Follows nextPageToken to fetch all events."""
        page1 = {
            "items": [{"id": "e1", "summary": "A", "start": {"dateTime": "2026-03-11T09:00:00Z"}, "end": {"dateTime": "2026-03-11T10:00:00Z"}, "status": "confirmed"}],
            "nextPageToken": "token2",
        }
        page2 = {
            "items": [{"id": "e2", "summary": "B", "start": {"dateTime": "2026-03-11T11:00:00Z"}, "end": {"dateTime": "2026-03-11T12:00:00Z"}, "status": "confirmed"}],
        }
        mock_service = MagicMock()
        mock_list = mock_service.events.return_value.list
        mock_list.return_value.execute.side_effect = [page1, page2]
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00Z",
            time_max="2026-03-12T00:00:00Z",
        )

        assert len(result) == 2
        assert result[0]["title"] == "A"
        assert result[1]["title"] == "B"

    @pytest.mark.asyncio
    async def test_declined_event_included(self, adapter: GcalAdapter):
        """Declined events are included with your_status='declined'."""
        api_event = {
            "id": "evt3",
            "summary": "Skipped Meeting",
            "start": {"dateTime": "2026-03-11T14:00:00+01:00"},
            "end": {"dateTime": "2026-03-11T15:00:00+01:00"},
            "status": "confirmed",
            "attendees": [
                {"email": "test@gmail.com", "responseStatus": "declined", "self": True},
            ],
        }
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [api_event],
        }
        adapter._service = mock_service

        result = await adapter.list_events(
            time_min="2026-03-11T00:00:00+01:00",
            time_max="2026-03-12T00:00:00+01:00",
        )

        assert result[0]["your_status"] == "declined"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gcal_adapter.py::TestListEvents -v`
Expected: FAIL — `AttributeError: 'GcalAdapter' object has no attribute 'list_events'`

- [ ] **Step 3: Implement list_events**

Add to `src/ts4k/adapters/gcal.py` in the `GcalAdapter` class:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gcal_adapter.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/adapters/gcal.py tests/test_gcal_adapter.py
git commit -m "feat: add list_events with pagination, normalization, recurring ID"
```

---

### Task 6: Add read_event and list_calendars

**Files:**
- Modify: `src/ts4k/adapters/gcal.py`
- Modify: `tests/test_gcal_adapter.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gcal_adapter.py`:

```python
class TestReadEvent:
    @pytest.mark.asyncio
    async def test_full_detail(self, adapter: GcalAdapter):
        api_event = {
            "id": "evt1",
            "summary": "Budget Review",
            "start": {"dateTime": "2026-03-11T11:00:00+01:00"},
            "end": {"dateTime": "2026-03-11T12:00:00+01:00"},
            "status": "confirmed",
            "organizer": {"email": "sarah@work.com", "displayName": "Sarah Chen"},
            "description": "Review Q1 numbers.",
            "location": "Room 4A",
            "attendees": [
                {"email": "sarah@work.com", "displayName": "Sarah Chen", "responseStatus": "accepted"},
                {"email": "test@gmail.com", "responseStatus": "accepted", "self": True},
            ],
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU"],
            "created": "2026-01-15T10:00:00.000Z",
            "updated": "2026-03-10T14:30:00.000Z",
            "conferenceData": {
                "entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/xyz"}],
            },
        }
        mock_service = MagicMock()
        mock_service.events.return_value.get.return_value.execute.return_value = api_event
        adapter._service = mock_service

        result = await adapter.read_event("gc:evt1")

        assert result["description"] == "Review Q1 numbers."
        assert result["meeting_link"] == "https://meet.google.com/xyz"
        assert len(result["attendees"]) == 2
        assert result["attendees"][0]["name"] == "Sarah Chen"
        assert result["recurrence"] == "FREQ=WEEKLY;BYDAY=TU"
        assert result["created"] == "2026-01-15T10:00:00.000Z"

    @pytest.mark.asyncio
    async def test_strips_prefix(self, adapter: GcalAdapter):
        """Event ID prefix is stripped before API call."""
        mock_service = MagicMock()
        mock_service.events.return_value.get.return_value.execute.return_value = {
            "id": "evt1", "summary": "X",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
        }
        adapter._service = mock_service

        await adapter.read_event("gc:evt1")

        call_args = mock_service.events.return_value.get.call_args
        assert call_args[1]["eventId"] == "evt1"


class TestListCalendars:
    @pytest.mark.asyncio
    async def test_filters_freebusy_only(self, adapter: GcalAdapter):
        """freeBusyReader calendars are excluded."""
        mock_service = MagicMock()
        mock_service.calendarList.return_value.list.return_value.execute.return_value = {
            "items": [
                {"id": "primary", "summary": "Main", "accessRole": "owner", "timeZone": "Europe/Amsterdam"},
                {"id": "holidays", "summary": "Holidays", "accessRole": "reader", "timeZone": "Europe/Amsterdam"},
                {"id": "freebusy", "summary": "Busy", "accessRole": "freeBusyReader", "timeZone": "UTC"},
            ],
        }
        adapter._service = mock_service

        result = await adapter.list_calendars()

        assert len(result) == 2
        assert result[0]["id"] == "primary"
        assert result[1]["id"] == "holidays"

    @pytest.mark.asyncio
    async def test_pagination(self, adapter: GcalAdapter):
        """Follows nextPageToken."""
        mock_service = MagicMock()
        page1 = {"items": [{"id": "c1", "summary": "A", "accessRole": "owner", "timeZone": "UTC"}], "nextPageToken": "tok"}
        page2 = {"items": [{"id": "c2", "summary": "B", "accessRole": "owner", "timeZone": "UTC"}]}
        mock_service.calendarList.return_value.list.return_value.execute.side_effect = [page1, page2]
        adapter._service = mock_service

        result = await adapter.list_calendars()
        assert len(result) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gcal_adapter.py::TestReadEvent tests/test_gcal_adapter.py::TestListCalendars -v`
Expected: FAIL — methods don't exist

- [ ] **Step 3: Implement read_event and list_calendars**

Add to `GcalAdapter` in `src/ts4k/adapters/gcal.py`:

```python
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
```

Also add a module-level `rrule_to_human` function (placeholder — fleshed out in Chunk 3):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gcal_adapter.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/adapters/gcal.py tests/test_gcal_adapter.py
git commit -m "feat: add read_event, list_calendars, rrule_to_human to GcalAdapter"
```

---

### Task 7: Add write operations (create_event, update_event, rsvp)

**Files:**
- Modify: `src/ts4k/adapters/gcal.py`
- Create: `tests/test_gcal_write.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gcal_write.py`:

```python
"""Tests for GcalAdapter write operations."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from ts4k.adapters.gcal import GcalAdapter, GcalAdapterConfig
from ts4k.core.levels import AccessLevel


def _make_adapter(level: str = "send", prefix: str = "gc") -> GcalAdapter:
    config = GcalAdapterConfig(
        email="test@gmail.com",
        calendar_id="primary",
        calendar_name="Test",
        timezone="Europe/Amsterdam",
        level=level,
    )
    adapter = GcalAdapter(config, prefix=prefix)
    adapter._service = MagicMock()
    return adapter


class TestCreateEventLevelGating:
    @pytest.mark.asyncio
    async def test_readonly_rejected(self):
        adapter = _make_adapter(level="readonly")
        with pytest.raises(PermissionError, match="draft"):
            await adapter.create_event(title="Test", start="2026-03-11T09:00:00Z", end="2026-03-11T10:00:00Z")

    @pytest.mark.asyncio
    async def test_draft_allowed_no_attendees(self):
        adapter = _make_adapter(level="draft")
        adapter._service.events.return_value.insert.return_value.execute.return_value = {
            "id": "new1", "summary": "Test",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
        }
        result = await adapter.create_event(title="Test", start="2026-03-11T09:00:00Z", end="2026-03-11T10:00:00Z")
        assert result["id"] == "gc:new1"

    @pytest.mark.asyncio
    async def test_draft_rejected_with_attendees(self):
        adapter = _make_adapter(level="draft")
        with pytest.raises(PermissionError, match="send"):
            await adapter.create_event(
                title="Test", start="2026-03-11T09:00:00Z", end="2026-03-11T10:00:00Z",
                attendees=["alice@example.com"],
            )

    @pytest.mark.asyncio
    async def test_send_allowed_with_attendees(self):
        adapter = _make_adapter(level="send")
        adapter._service.events.return_value.insert.return_value.execute.return_value = {
            "id": "new2", "summary": "Team Sync",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
            "attendees": [{"email": "alice@example.com"}],
        }
        result = await adapter.create_event(
            title="Team Sync", start="2026-03-11T09:00:00Z", end="2026-03-11T10:00:00Z",
            attendees=["alice@example.com"],
        )
        assert result["id"] == "gc:new2"


class TestCreateEventAllDay:
    @pytest.mark.asyncio
    async def test_inclusive_end_date_converted(self):
        """CLI provides inclusive end date; adapter adds +1 day for API."""
        adapter = _make_adapter(level="draft")
        adapter._service.events.return_value.insert.return_value.execute.return_value = {
            "id": "new3", "summary": "Vacation",
            "start": {"date": "2026-03-17"},
            "end": {"date": "2026-03-22"},
            "status": "confirmed",
        }
        await adapter.create_event(title="Vacation", start="2026-03-17", end="2026-03-21")

        call_args = adapter._service.events.return_value.insert.call_args
        body = call_args[1]["body"]
        assert body["start"] == {"date": "2026-03-17"}
        assert body["end"] == {"date": "2026-03-22"}  # +1 day


class TestUpdateEvent:
    @pytest.mark.asyncio
    async def test_modify_level_required(self):
        adapter = _make_adapter(level="readonly")
        with pytest.raises(PermissionError):
            await adapter.update_event("gc:evt1", title="New Title")

    @pytest.mark.asyncio
    async def test_patches_fields(self):
        adapter = _make_adapter(level="modify")
        adapter._service.events.return_value.patch.return_value.execute.return_value = {
            "id": "evt1", "summary": "New Title",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
        }
        result = await adapter.update_event("gc:evt1", title="New Title")
        assert result["title"] == "New Title"


class TestRsvp:
    @pytest.mark.asyncio
    async def test_modify_level_required(self):
        adapter = _make_adapter(level="readonly")
        with pytest.raises(PermissionError):
            await adapter.rsvp("gc:evt1", status="accepted")

    @pytest.mark.asyncio
    async def test_updates_self_status(self):
        adapter = _make_adapter(level="modify")
        adapter._service.events.return_value.get.return_value.execute.return_value = {
            "id": "evt1", "summary": "Meeting",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
            "attendees": [
                {"email": "test@gmail.com", "self": True, "responseStatus": "needsAction"},
                {"email": "other@work.com", "responseStatus": "accepted"},
            ],
        }
        adapter._service.events.return_value.patch.return_value.execute.return_value = {
            "id": "evt1", "summary": "Meeting",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
            "attendees": [
                {"email": "test@gmail.com", "self": True, "responseStatus": "accepted"},
                {"email": "other@work.com", "responseStatus": "accepted"},
            ],
        }
        result = await adapter.rsvp("gc:evt1", status="accepted")
        assert result["your_status"] == "accepted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gcal_write.py -v`
Expected: FAIL — methods don't exist

- [ ] **Step 3: Implement create_event, update_event, rsvp**

Add to `GcalAdapter` in `src/ts4k/adapters/gcal.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gcal_write.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run all adapter tests together**

Run: `uv run pytest tests/test_gcal_adapter.py tests/test_gcal_write.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/ts4k/adapters/gcal.py tests/test_gcal_write.py
git commit -m "feat: add create_event, update_event, rsvp to GcalAdapter"
```

---

## Chunk 3: Format — Event Output

### Task 8: Add format_events with adaptive time column

**Files:**
- Modify: `src/ts4k/core/format.py`
- Create: `tests/test_cal_format.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cal_format.py`:

```python
"""Tests for calendar event formatting."""

from __future__ import annotations

import pytest


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cal_format.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_events'`

- [ ] **Step 3: Implement format_events, format_event_detail, and collapsing**

Add to `src/ts4k/core/format.py` (after existing format functions, around line 147):

```python
# -- Calendar event formatting ------------------------------------------------

def format_events(
    events: list[dict],
    fmt: str = "pipe",
    ref_table: "RefTable | None" = None,
    collapse_recurring: bool = False,
) -> str:
    """Format calendar events as pipe-delimited listing.

    Args:
        events: Normalized event dicts from GcalAdapter.
        fmt: Output format (pipe, json, xml).
        ref_table: If provided, assigns short refs via ref_table.assign().
        collapse_recurring: If True, collapse recurring series into one row.
    """
    if fmt == "json":
        import json
        return json.dumps(events, indent=2)

    if not events:
        return "No events."

    # Determine time display mode from date span
    time_mode = _detect_time_mode(events)

    # Optionally collapse recurring events
    display_events = _collapse_recurring(events) if collapse_recurring else events

    # Assign refs (same pattern as format_listing)
    ref_map = ref_table.assign(display_events) if ref_table else None

    lines = ["REF|SOURCE|TIME|DUR|TITLE|LOCATION|ATTENDEES"]
    ref_num = 0
    for evt in display_events:
        ref_num += 1

        time_str = _format_event_time(evt, time_mode)
        dur_str = _format_duration(evt)
        title = evt.get("title", "")
        if evt.get("your_status") == "declined":
            title += " (declined)"
        # Add recurrence annotation if present and collapsing
        recurrence_note = ""
        if collapse_recurring and evt.get("_collapsed"):
            summary = evt.get("recurrence_summary", "recurring")
            recurrence_note = f" ({summary})"

        location = evt.get("location", "")
        attendees = evt.get("attendees_summary", "")
        if recurrence_note:
            attendees = f"{attendees} {recurrence_note}".strip() if attendees else recurrence_note.strip()

        lines.append(f"{ref_num}|{evt.get('source', '')}|{time_str}|{dur_str}|{title}|{location}|{attendees}")

    return "\n".join(lines)


def format_event_detail(event: dict, ref: int = 0, fmt: str = "pipe") -> str:
    """Format a single event's full detail as mini-XML."""
    if fmt == "json":
        import json
        return json.dumps(event, indent=2)

    when = _format_when_detail(event)
    parts = [f'<ev ref="{ref}" id="{event.get("id", "")}">']
    parts.append(f'<title>{event.get("title", "")}</title>')
    parts.append(f"<when>{when}</when>")

    if event.get("location"):
        parts.append(f'<where>{event["location"]}</where>')
    if event.get("organizer"):
        parts.append(f'<organizer>{event["organizer"]}</organizer>')
    if event.get("your_status"):
        parts.append(f'<your-status>{event["your_status"]}</your-status>')

    attendees = event.get("attendees", [])
    if attendees:
        att_lines = []
        for a in attendees:
            name = a.get("name", a.get("email", ""))
            status = a.get("status", "")
            att_lines.append(f"  {name} ({status})")
        parts.append("<attendees>")
        parts.extend(att_lines)
        parts.append("</attendees>")

    if event.get("meeting_link"):
        parts.append(f'<link>{event["meeting_link"]}</link>')
    if event.get("recurrence_summary"):
        parts.append(f'<recurrence>{event["recurrence_summary"]}</recurrence>')
    if event.get("description"):
        parts.append(f'<description>{event["description"]}</description>')

    parts.append("</ev>")
    return "\n".join(parts)


# -- Calendar format helpers --------------------------------------------------

def _detect_time_mode(events: list[dict]) -> str:
    """Determine time display mode based on date span of events.

    Returns: 'time' (same day), 'day' (multi-day <=7d), 'date' (>7d).
    """
    from datetime import datetime

    dates = set()
    for evt in events:
        start = evt.get("start", "")
        if evt.get("all_day"):
            dates.add(start)
        else:
            # Extract date part from ISO datetime
            dates.add(start[:10])

    if len(dates) <= 1:
        return "time"

    sorted_dates = sorted(dates)
    try:
        first = datetime.strptime(sorted_dates[0], "%Y-%m-%d")
        last = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
        span = (last - first).days
    except ValueError:
        return "date"

    return "day" if span <= 7 else "date"


def _format_event_time(event: dict, mode: str) -> str:
    """Format event time based on display mode."""
    from datetime import datetime

    if event.get("all_day"):
        start = event["start"]
        end = event.get("end", start)
        if mode == "time":
            return "all-day"
        # Subtract 1 day from exclusive end for display
        try:
            end_dt = datetime.strptime(end, "%Y-%m-%d") - timedelta(days=1)
            end_display = f"{end_dt.strftime('%b')} {end_dt.day}" if end != start else ""
        except ValueError:
            end_display = ""
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        if mode == "day":
            day_name = start_dt.strftime("%a")
            return f"{day_name} all-day"
        # date mode
        start_display = f"{start_dt.strftime('%b')} {start_dt.day}"
        if end_display and end_display != start_display:
            return f"{start_display}-{end_dt.day}"
        return f"{start_display} all-day"

    # Timed event
    try:
        start_dt = datetime.fromisoformat(event["start"])
        end_dt = datetime.fromisoformat(event["end"])
    except (ValueError, KeyError):
        return event.get("start", "?")

    start_time = start_dt.strftime("%H:%M")
    end_time = end_dt.strftime("%H:%M")

    if mode == "time":
        return f"{start_time}-{end_time}"
    elif mode == "day":
        day_name = start_dt.strftime("%a")
        return f"{day_name} {start_time}-{end_time}"
    else:
        date_str = f"{start_dt.strftime('%b')} {start_dt.day}"
        return f"{date_str} {start_time}-{end_time}"


def _format_duration(event: dict) -> str:
    """Format duration for pipe output."""
    if event.get("all_day"):
        # Compute days from start/end dates
        try:
            from datetime import datetime
            s = datetime.strptime(event["start"], "%Y-%m-%d")
            e = datetime.strptime(event["end"], "%Y-%m-%d")
            days = (e - s).days
            return f"{days}d" if days > 1 else ""
        except (ValueError, KeyError):
            return ""

    mins = event.get("duration_minutes")
    if mins is None:
        return ""
    if mins >= 60 and mins % 60 == 0:
        return f"{mins // 60}h"
    if mins >= 60:
        return f"{mins // 60}h{mins % 60}m"
    return f"{mins}m"


def _format_when_detail(event: dict) -> str:
    """Format when line for event detail XML."""
    from datetime import datetime

    if event.get("all_day"):
        try:
            s = datetime.strptime(event["start"], "%Y-%m-%d")
            e = datetime.strptime(event["end"], "%Y-%m-%d") - timedelta(days=1)
            start_str = f"{s.strftime('%a %b')} {s.day}"
            if s.date() == e.date():
                return f"{start_str}, all-day"
            end_str = f"{e.strftime('%b')} {e.day}"
            days = (e - s).days + 1
            return f"{start_str}-{end_str} ({days}d)"
        except ValueError:
            return "all-day"

    try:
        s = datetime.fromisoformat(event["start"])
        e = datetime.fromisoformat(event["end"])
        day_str = f"{s.strftime('%a %b')} {s.day}"
        time_str = f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}"
        dur = _format_duration(event)
        return f"{day_str}, {time_str} ({dur})" if dur else f"{day_str}, {time_str}"
    except (ValueError, KeyError):
        return event.get("start", "?")


def _collapse_recurring(events: list[dict]) -> list[dict]:
    """Collapse recurring event instances into single representative rows.

    Groups by recurringEventId. For groups with 2+ instances,
    keep only the first (next upcoming) and mark it as collapsed.
    """
    from collections import OrderedDict

    groups: OrderedDict[str | None, list[dict]] = OrderedDict()
    for evt in events:
        key = evt.get("recurring_event_id")
        if key is None:
            # Non-recurring: use event ID as unique key
            groups[evt["id"]] = [evt]
        else:
            groups.setdefault(key, []).append(evt)

    result = []
    for key, group in groups.items():
        if len(group) == 1 or group[0].get("recurring_event_id") is None:
            result.append(group[0])
        else:
            # Collapse: take first instance, mark it
            representative = dict(group[0])
            representative["_collapsed"] = True
            representative["_instance_count"] = len(group)
            result.append(representative)

    return result
```

Also update the existing import at the top of `format.py` from:

```python
from datetime import datetime, timezone
```

to:

```python
from datetime import datetime, timedelta, timezone
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cal_format.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run all format tests**

Run: `uv run pytest tests/test_format.py tests/test_cal_format.py -v`
Expected: ALL PASS (existing format tests unbroken)

- [ ] **Step 6: Commit**

```bash
git add src/ts4k/core/format.py tests/test_cal_format.py
git commit -m "feat: add format_events, format_event_detail with adaptive time and recurring collapse"
```

---

## Chunk 4: Commands + Adapter Factory

### Task 9: Wire GcalAdapter into commands.py (factory + provider aliases)

**Files:**
- Modify: `src/ts4k/commands.py:21-23,69-118,135`

- [ ] **Step 1: Add GcalAdapter import alongside other adapters**

At `src/ts4k/commands.py` line 21-23, add:

```python
from ts4k.adapters.gcal import GcalAdapter, GcalAdapterConfig
```

- [ ] **Step 2: Add gcal branch to _make_adapter**

In `_make_adapter()` (around line 69-118), add a new `elif` branch:

```python
    elif provider == "gcal":
        email = cfg.get("email")
        calendar_id = cfg.get("calendar_id")
        if not email or not calendar_id:
            return None
        config = GcalAdapterConfig(
            email=email,
            calendar_id=calendar_id,
            calendar_name=cfg.get("calendar_name", calendar_id),
            timezone=cfg.get("timezone", "UTC"),
            config_dir=Path(cfg["config_dir"]) if cfg.get("config_dir") else None,
            level=cfg.get("level", "readonly"),
        )
        return GcalAdapter(config, prefix=prefix)
```

Update the return type annotation to include `GcalAdapter`.

- [ ] **Step 3: Add provider aliases**

In `provider_map` (line 135), add:

```python
    "google-calendar": "gcal", "calendar": "gcal", "cal": "gcal",
```

- [ ] **Step 4: Run existing tests to confirm nothing broken**

Run: `uv run pytest tests/test_commands.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/commands.py
git commit -m "feat: wire GcalAdapter into commands.py factory and provider aliases"
```

---

### Task 10: Add cal_today, cal_tomorrow, cal_week, cal_next, cal_range commands

**Files:**
- Modify: `src/ts4k/commands.py`
- Create: `tests/test_cal_commands.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cal_commands.py`:

```python
"""Tests for calendar command functions."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from ts4k.commands import cal_today, cal_week, cal_next, cal_event


@pytest.fixture
def mock_sources(tmp_path, monkeypatch):
    """Set up mock sources with a gcal source."""
    from ts4k.state import sources
    sources_file = tmp_path / "sources.json"
    sources_file.write_text('{"gc": {"provider": "gcal", "email": "test@gmail.com", "calendar_id": "primary", "calendar_name": "Main", "timezone": "UTC", "level": "readonly"}}')
    monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_SOURCES_FILE", sources_file)


class TestCalToday:
    @pytest.mark.asyncio
    async def test_returns_command_result(self, mock_sources):
        mock_events = [
            {"id": "gc:e1", "source": "gc", "title": "Standup", "start": "2026-03-11T09:00:00Z", "end": "2026-03-11T09:30:00Z", "all_day": False, "duration_minutes": 30, "location": "Zoom", "attendees_summary": "3 people", "status": "confirmed", "your_status": None, "recurring_event_id": None},
        ]
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


class TestCalEvent:
    @pytest.mark.asyncio
    async def test_returns_detail(self, mock_sources):
        mock_event = {
            "id": "gc:e1", "source": "gc", "title": "Budget Review",
            "start": "2026-03-11T11:00:00Z", "end": "2026-03-11T12:00:00Z",
            "all_day": False, "duration_minutes": 60, "location": "Room 4A",
            "organizer": "sarah@work.com", "status": "confirmed", "your_status": "accepted",
            "attendees": [{"name": "Sarah", "email": "sarah@work.com", "status": "accepted"}],
            "description": "Review Q1.", "meeting_link": "", "recurrence_summary": "",
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cal_commands.py -v`
Expected: FAIL — `cannot import name 'cal_today'`

- [ ] **Step 3: Implement calendar command functions**

Add to `src/ts4k/commands.py` (at the end, before any `if __name__` block):

```python
# -- Calendar commands ---------------------------------------------------------

async def _cal_fetch_events(
    source: str | None,
    time_min: str,
    time_max: str,
    count: int = 250,
) -> list[dict]:
    """Fetch events from all gcal sources (or a specific one), merge by start time."""
    from ts4k.state import sources as src_mod

    all_sources = src_mod.list_all()
    prefixes = []
    for pfx, cfg in all_sources.items():
        if cfg.get("provider") != "gcal":
            continue
        if source and pfx != source and cfg.get("provider") != source:
            continue
        prefixes.append((pfx, cfg))

    if not prefixes:
        return []

    all_events: list[dict] = []
    for pfx, cfg in prefixes:
        adapter = _make_adapter(pfx, cfg)
        if adapter is None:
            continue
        async with adapter:
            events = await adapter.list_events(time_min=time_min, time_max=time_max, count=count)
            all_events.extend(events)

    # Sort by start time
    all_events.sort(key=lambda e: e.get("start", ""))
    return all_events[:count]


def _cal_time_bounds(day_offset: int = 0, days: int = 1, timezone: str = "UTC") -> tuple[str, str]:
    """Compute time_min and time_max for calendar queries."""
    from datetime import datetime, timezone as tz
    import zoneinfo

    try:
        tzinfo = zoneinfo.ZoneInfo(timezone)
    except Exception:
        tzinfo = tz.utc

    now = datetime.now(tzinfo)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
    end = start + timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _get_cal_timezone(source: str | None) -> str:
    """Get timezone from calendar sources config."""
    from ts4k.state import sources as src_mod

    all_sources = src_mod.list_all()
    for pfx, cfg in all_sources.items():
        if cfg.get("provider") != "gcal":
            continue
        if source and pfx != source:
            continue
        return cfg.get("timezone", "UTC")
    return "UTC"


async def cal_today(source: str | None, fmt: str, ref_table: RefTable | None = None) -> CommandResult:
    """Today's calendar events."""
    tz = _get_cal_timezone(source)
    time_min, time_max = _cal_time_bounds(day_offset=0, days=1, timezone=tz)
    events = await _cal_fetch_events(source, time_min, time_max)
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=False)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_tomorrow(source: str | None, fmt: str, ref_table: RefTable | None = None) -> CommandResult:
    """Tomorrow's calendar events."""
    tz = _get_cal_timezone(source)
    time_min, time_max = _cal_time_bounds(day_offset=1, days=1, timezone=tz)
    events = await _cal_fetch_events(source, time_min, time_max)
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=False)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_week(source: str | None, fmt: str, ref_table: RefTable | None = None) -> CommandResult:
    """This week's calendar events (Mon-Sun)."""
    tz = _get_cal_timezone(source)
    # Compute actual Monday-Sunday range for current week
    import zoneinfo
    from datetime import timezone as _tz
    try:
        tzinfo = zoneinfo.ZoneInfo(tz)
    except Exception:
        tzinfo = _tz.utc
    now = datetime.now(tzinfo)
    monday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    sunday_end = monday + timedelta(days=7)
    time_min, time_max = monday.isoformat(), sunday_end.isoformat()
    events = await _cal_fetch_events(source, time_min, time_max)
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=False)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_next(source: str | None, count: int, fmt: str, ref_table: RefTable | None = None) -> CommandResult:
    """Next N events from now, any timeframe."""
    tz = _get_cal_timezone(source)
    # Look ahead 365 days max, collapse recurring
    time_min, _ = _cal_time_bounds(day_offset=0, days=1, timezone=tz)
    _, time_max = _cal_time_bounds(day_offset=0, days=365, timezone=tz)
    events = await _cal_fetch_events(source, time_min, time_max, count=count)
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=True)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_range(
    source: str | None, from_date: str, to_date: str, fmt: str,
    ref_table: RefTable | None = None,
) -> CommandResult:
    """Events in an arbitrary date range."""
    from datetime import datetime
    import zoneinfo

    tz_name = _get_cal_timezone(source)
    try:
        tzinfo = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        from datetime import timezone
        tzinfo = timezone.utc

    start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=tzinfo)
    end = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=tzinfo)
    collapse = (end - start).days > 7
    events = await _cal_fetch_events(source, start.isoformat(), end.isoformat())
    output = format_events(events, fmt=fmt, ref_table=ref_table, collapse_recurring=collapse)
    return CommandResult(output=output, messages_processed=len(events))


async def cal_event(
    ref_or_id: str, source: str | None, fmt: str,
    ref_table: RefTable | None = None,
) -> str:
    """Full detail for a single event."""
    from ts4k.state import sources as src_mod

    # Resolve ref to event ID
    event_id = ref_or_id
    if ref_table is not None and (ref_or_id.isdigit() or ref_or_id.startswith("#")):
        resolved = ref_table.resolve(ref_or_id)
        if resolved is not None:
            event_id = resolved

    # Find the right adapter from the prefix
    prefix = event_id.split(":")[0] if ":" in event_id else source
    all_sources = src_mod.list_all()
    cfg = all_sources.get(prefix)
    if not cfg or cfg.get("provider") != "gcal":
        return f"Error: no gcal source with prefix '{prefix}'"

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return f"Error: could not create adapter for '{prefix}'"

    async with adapter:
        event = await adapter.read_event(event_id)

    ref_num = int(ref_or_id) if ref_or_id.isdigit() else 0
    return format_event_detail(event, ref=ref_num, fmt=fmt)


async def cal_list_calendars(email: str, config_dir: Path | None = None) -> list[dict]:
    """List available calendars for a Google account (non-interactive, for setup wizard)."""
    from ts4k.adapters.gcal import GcalAdapter, GcalAdapterConfig

    config = GcalAdapterConfig(
        email=email, calendar_id="primary", calendar_name="",
        timezone="UTC", config_dir=config_dir, level="readonly",
    )
    adapter = GcalAdapter(config, prefix="_setup")
    async with adapter:
        return await adapter.list_calendars()


async def cal_create(
    source: str, title: str, start: str, end: str,
    description: str | None, location: str | None,
    attendees: list[str] | None,
    ref_table: RefTable | None = None,
) -> str:
    """Create a calendar event."""
    from ts4k.state import sources as src_mod

    cfg = src_mod.list_all().get(source)
    if not cfg or cfg.get("provider") != "gcal":
        return f"Error: '{source}' is not a gcal source"

    adapter = _make_adapter(source, cfg)
    if adapter is None:
        return f"Error: could not create adapter for '{source}'"

    async with adapter:
        event = await adapter.create_event(
            title=title, start=start, end=end,
            description=description, location=location, attendees=attendees,
        )

    return f"Created: {event['title']} ({event['id']})"


async def cal_update(ref_or_id: str, source: str | None, ref_table: RefTable | None = None, **fields) -> str:
    """Update a calendar event."""
    from ts4k.state import sources as src_mod

    event_id = ref_or_id
    if ref_table is not None and (ref_or_id.isdigit() or ref_or_id.startswith("#")):
        resolved = ref_table.resolve(ref_or_id)
        if resolved is not None:
            event_id = resolved

    prefix = event_id.split(":")[0] if ":" in event_id else source
    cfg = src_mod.list_all().get(prefix)
    if not cfg or cfg.get("provider") != "gcal":
        return f"Error: no gcal source with prefix '{prefix}'"

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return f"Error: could not create adapter for '{prefix}'"

    async with adapter:
        event = await adapter.update_event(event_id, **fields)

    return f"Updated: {event['title']} ({event['id']})"


async def cal_rsvp(ref_or_id: str, source: str | None, status: str, ref_table: RefTable | None = None) -> str:
    """RSVP to a calendar event."""
    from ts4k.state import sources as src_mod

    event_id = ref_or_id
    if ref_table is not None and (ref_or_id.isdigit() or ref_or_id.startswith("#")):
        resolved = ref_table.resolve(ref_or_id)
        if resolved is not None:
            event_id = resolved

    prefix = event_id.split(":")[0] if ":" in event_id else source
    cfg = src_mod.list_all().get(prefix)
    if not cfg or cfg.get("provider") != "gcal":
        return f"Error: no gcal source with prefix '{prefix}'"

    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return f"Error: could not create adapter for '{prefix}'"

    async with adapter:
        event = await adapter.rsvp(event_id, status=status)

    return f"RSVP {status}: {event['title']} ({event['id']})"
```

Add the `format_events` and `format_event_detail` imports at the top of `commands.py`:

```python
from ts4k.core.format import format_events, format_event_detail
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cal_commands.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run all command tests**

Run: `uv run pytest tests/test_commands.py tests/test_cal_commands.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/ts4k/commands.py tests/test_cal_commands.py
git commit -m "feat: add calendar command functions (today/week/next/range/event/create/update/rsvp)"
```

---

## Chunk 5: CLI + Auth Integration

### Task 11: Add cal subparser to CLI

**Files:**
- Modify: `src/ts4k/cli.py`

- [ ] **Step 1: Add the cal subparser block**

In `src/ts4k/cli.py`, after the last subparser block (status at ~line 1083), add the `cal` subparser with sub-subparsers:

```python
    # -- cal -------------------------------------------------------------------
    cal_parser = subparsers.add_parser("cal", help="Calendar events")
    cal_parser.add_argument("--source", "-s", default=None)
    cal_parser.add_argument("--format", "-f", default="pipe")
    cal_subs = cal_parser.add_subparsers(dest="cal_cmd")
    cal_parser.set_defaults(func=_cmd_cal)

    # cal today (default)
    cal_today_p = cal_subs.add_parser("today", help="Today's events")
    cal_today_p.add_argument("--source", "-s", default=None)
    cal_today_p.add_argument("--format", "-f", default="pipe")
    cal_today_p.set_defaults(func=_cmd_cal_today)

    # cal tomorrow
    cal_tmrw_p = cal_subs.add_parser("tomorrow", help="Tomorrow's events")
    cal_tmrw_p.add_argument("--source", "-s", default=None)
    cal_tmrw_p.add_argument("--format", "-f", default="pipe")
    cal_tmrw_p.set_defaults(func=_cmd_cal_tomorrow)

    # cal week
    cal_week_p = cal_subs.add_parser("week", help="This week's events")
    cal_week_p.add_argument("--source", "-s", default=None)
    cal_week_p.add_argument("--format", "-f", default="pipe")
    cal_week_p.set_defaults(func=_cmd_cal_week)

    # cal next
    cal_next_p = cal_subs.add_parser("next", help="Next N events")
    cal_next_p.add_argument("-n", type=int, default=10)
    cal_next_p.add_argument("--source", "-s", default=None)
    cal_next_p.add_argument("--format", "-f", default="pipe")
    cal_next_p.set_defaults(func=_cmd_cal_next)

    # cal range
    cal_range_p = cal_subs.add_parser("range", help="Events in date range")
    cal_range_p.add_argument("--from", dest="from_date", required=True)
    cal_range_p.add_argument("--to", dest="to_date", required=True)
    cal_range_p.add_argument("--source", "-s", default=None)
    cal_range_p.add_argument("--format", "-f", default="pipe")
    cal_range_p.set_defaults(func=_cmd_cal_range)

    # cal event
    cal_event_p = cal_subs.add_parser("event", help="Event detail")
    cal_event_p.add_argument("ref", help="Event ref or ID")
    cal_event_p.add_argument("--source", "-s", default=None)
    cal_event_p.add_argument("--format", "-f", default="pipe")
    cal_event_p.set_defaults(func=_cmd_cal_event)

    # cal setup
    cal_setup_p = cal_subs.add_parser("setup", help="Discover and add calendar sources")
    cal_setup_p.set_defaults(func=_cmd_cal_setup)

    # cal create
    cal_create_p = cal_subs.add_parser("create", help="Create an event")
    cal_create_p.add_argument("--title", required=True)
    cal_create_p.add_argument("--start", required=True)
    cal_create_p.add_argument("--end", required=True)
    cal_create_p.add_argument("--description", default=None)
    cal_create_p.add_argument("--location", default=None)
    cal_create_p.add_argument("--attendees", default=None, help="Comma-separated emails")
    cal_create_p.add_argument("--source", "-s", required=True)
    cal_create_p.set_defaults(func=_cmd_cal_create)

    # cal update
    cal_update_p = cal_subs.add_parser("update", help="Update an event")
    cal_update_p.add_argument("ref", help="Event ref or ID")
    cal_update_p.add_argument("--title", default=None)
    cal_update_p.add_argument("--start", default=None)
    cal_update_p.add_argument("--end", default=None)
    cal_update_p.add_argument("--description", default=None)
    cal_update_p.add_argument("--location", default=None)
    cal_update_p.add_argument("--source", "-s", default=None)
    cal_update_p.set_defaults(func=_cmd_cal_update)

    # cal rsvp
    cal_rsvp_p = cal_subs.add_parser("rsvp", help="RSVP to an event")
    cal_rsvp_p.add_argument("ref", help="Event ref or ID")
    cal_rsvp_p.add_argument("--status", required=True, choices=["accepted", "declined", "tentative"])
    cal_rsvp_p.add_argument("--source", "-s", default=None)
    cal_rsvp_p.set_defaults(func=_cmd_cal_rsvp)
```

- [ ] **Step 2: Add the handler functions**

Add handler functions in `cli.py`:

```python
async def _cmd_cal(args):
    """Default: show today's events."""
    return await _cmd_cal_today(args)


async def _cmd_cal_today(args):
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_today(
        source=getattr(args, "source", None),
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_tomorrow(args):
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_tomorrow(
        source=getattr(args, "source", None),
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_week(args):
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_week(
        source=getattr(args, "source", None),
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_next(args):
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_next(
        source=getattr(args, "source", None),
        count=args.n,
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_range(args):
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    result = await commands.cal_range(
        source=getattr(args, "source", None),
        from_date=args.from_date, to_date=args.to_date,
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    refs.save(_refs_path(getattr(args, "key", None)))
    print(result.output)


async def _cmd_cal_event(args):
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    output = await commands.cal_event(
        ref_or_id=args.ref,
        source=getattr(args, "source", None),
        fmt=getattr(args, "format", "pipe") or "pipe",
        ref_table=refs,
    )
    print(output)


async def _cmd_cal_create(args):
    attendees = [e.strip() for e in args.attendees.split(",")] if args.attendees else None
    output = await commands.cal_create(
        source=args.source, title=args.title, start=args.start, end=args.end,
        description=args.description, location=args.location,
        attendees=attendees,
    )
    print(output)


async def _cmd_cal_update(args):
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    fields = {}
    for f in ("title", "start", "end", "description", "location"):
        val = getattr(args, f, None)
        if val is not None:
            fields[f] = val
    output = await commands.cal_update(
        ref_or_id=args.ref,
        source=getattr(args, "source", None),
        ref_table=refs, **fields,
    )
    print(output)


async def _cmd_cal_rsvp(args):
    refs = RefTable()
    refs.load(_refs_path(getattr(args, "key", None)))
    output = await commands.cal_rsvp(
        ref_or_id=args.ref,
        source=getattr(args, "source", None),
        status=args.status, ref_table=refs,
    )
    print(output)


async def _cmd_cal_setup(args):
    """Interactive calendar setup wizard."""
    from ts4k.state import sources as src_mod

    all_sources = src_mod.list_all()

    # Find Google accounts from gmail sources
    google_emails = {}
    for pfx, cfg in all_sources.items():
        if cfg.get("provider") == "gmail":
            email = cfg.get("email")
            if email:
                google_emails[email] = pfx

    if not google_emails:
        print("No Gmail sources found. Add a Gmail source first: ts4k src add g gmail --email you@gmail.com")
        return

    # Collect all available calendars
    all_cals = []
    for email, gmail_prefix in google_emails.items():
        print(f"\nFound Google account: {email} (from source '{gmail_prefix}')")
        print(f"Fetching calendars for {email}...")
        try:
            cals = await commands.cal_list_calendars(email)
        except Exception as e:
            print(f"  Error: {e}")
            continue
        for cal in cals:
            # Skip already-configured calendars
            already = any(
                c.get("provider") == "gcal" and c.get("email") == email and c.get("calendar_id") == cal["id"]
                for c in all_sources.values()
            )
            if already:
                print(f"  (skipped: {cal['summary']} — already configured)")
                continue
            all_cals.append({"email": email, **cal})
            print(f"  {len(all_cals)}. {cal['summary']}" + (" (primary)" if cal.get("primary") else ""))

    if not all_cals:
        print("\nNo new calendars to add.")
        return

    # Selection
    choice = input("\nWhich calendars? (comma-separated, or 'all'): ").strip()
    if choice.lower() == "all":
        selected = all_cals
    else:
        indices = [int(i.strip()) - 1 for i in choice.split(",") if i.strip().isdigit()]
        selected = [all_cals[i] for i in indices if 0 <= i < len(all_cals)]

    if not selected:
        print("No calendars selected.")
        return

    # Assign prefixes and add
    for cal in selected:
        suggested = _suggest_cal_prefix(cal["summary"], all_sources)
        prefix = input(f"Prefix for '{cal['summary']}'? [{suggested}]: ").strip() or suggested

        if prefix in all_sources:
            print(f"  Prefix '{prefix}' already in use — skipping.")
            continue

        src_mod.add(
            prefix,
            provider="gcal",
            email=cal["email"],
            calendar_id=cal["id"],
            calendar_name=cal["summary"],
            timezone=cal.get("timezone", "UTC"),
            level="readonly",
        )
        all_sources[prefix] = {}  # Track for collision detection
        print(f"  Added '{cal['summary']}' as '{prefix}' (readonly)")

    print(f"\nAdded {len(selected)} calendar source(s).")


def _suggest_cal_prefix(name: str, existing: dict) -> str:
    """Suggest a short prefix for a calendar name."""
    base = "gc"
    # Use first letter of each word after 'gc'
    words = name.lower().replace("@", " ").replace(".", " ").split()
    if words and words[0] not in ("primary", "my"):
        suffix = words[0][:1]
        candidate = f"gc{suffix}"
    else:
        candidate = "gc"

    # Avoid collisions
    if candidate not in existing:
        return candidate
    for i in range(2, 10):
        if f"{candidate}{i}" not in existing:
            return f"{candidate}{i}"
    return candidate
```

`_cmd_cal_setup` is now async, so it does NOT go in the sync handlers list. It will be dispatched via `asyncio.run()` like other async handlers.

- [ ] **Step 3: Update auth command for gcal scope awareness**

In the auth handler `_cmd_auth` (around line 586-682), find where Gmail scopes are collected. Add gcal sources for the same email:

```python
# After collecting gmail scopes for the email, also check gcal sources
for pfx, cfg in all_sources.items():
    if cfg.get("provider") == "gcal" and cfg.get("email") == email:
        from ts4k.core.levels import scopes_for, parse_level
        gcal_level = parse_level(cfg.get("level"))
        gcal_scopes = scopes_for("gcal", gcal_level)
        all_scopes.extend(s for s in gcal_scopes if s not in all_scopes)
```

- [ ] **Step 4: Run CLI smoke test**

Run: `uv run ts4k cal --help`
Expected: Shows cal subcommands (today, tomorrow, week, next, range, event, setup, create, update, rsvp)

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/cli.py
git commit -m "feat: add cal CLI subcommand with setup wizard and auth integration"
```

---

## Chunk 6: MCP Server + Help

### Task 12: Add MCP tools for calendar

**Files:**
- Modify: `src/ts4k/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Add cal read tool**

In `src/ts4k/server.py`, after the last `@mcp.tool()` function (line ~273):

```python
@mcp.tool()
async def cal(
    view: str = "today",
    ref: str | None = None,
    source: str | None = None,
    count: int = 10,
    from_date: str | None = None,
    to_date: str | None = None,
    format: str = "pipe",
) -> str:
    """Calendar: view events across Google Calendar sources.

    Views: today, tomorrow, week, next, range, event.
    Use ref with view='event' for full detail.
    """
    if view == "event" and ref:
        return await commands.cal_event(
            ref_or_id=ref, source=source, fmt=format, ref_table=_refs,
        )
    elif view == "tomorrow":
        r = await commands.cal_tomorrow(source=source, fmt=format, ref_table=_refs)
    elif view == "week":
        r = await commands.cal_week(source=source, fmt=format, ref_table=_refs)
    elif view == "next":
        r = await commands.cal_next(source=source, count=count, fmt=format, ref_table=_refs)
    elif view == "range" and from_date and to_date:
        r = await commands.cal_range(
            source=source, from_date=from_date, to_date=to_date,
            fmt=format, ref_table=_refs,
        )
    else:  # default: today
        r = await commands.cal_today(source=source, fmt=format, ref_table=_refs)

    return r.error if r.error else r.output


@mcp.tool()
async def cal_create(
    source: str,
    title: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    attendees: str | None = None,
) -> str:
    """Create a calendar event.

    Requires draft level (no attendees) or send level (with attendees).
    For all-day events, use date format YYYY-MM-DD (inclusive).
    Attendees: comma-separated email addresses.
    """
    att_list = [e.strip() for e in attendees.split(",")] if attendees else None
    return await commands.cal_create(
        source=source, title=title, start=start, end=end,
        description=description, location=location,
        attendees=att_list, ref_table=_refs,
    )


@mcp.tool()
async def cal_manage(
    action: str,
    ref: str,
    source: str | None = None,
    status: str | None = None,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> str:
    """Modify a calendar event or RSVP.

    Actions: update, rsvp.
    RSVP requires --status (accepted/declined/tentative).
    Update accepts any combination of title, start, end, description, location.
    Requires modify level.
    """
    if action == "rsvp":
        if not status:
            return "Error: --status required for rsvp (accepted/declined/tentative)"
        return await commands.cal_rsvp(
            ref_or_id=ref, source=source, status=status, ref_table=_refs,
        )
    elif action == "update":
        fields = {}
        for name, val in [("title", title), ("start", start), ("end", end),
                          ("description", description), ("location", location)]:
            if val is not None:
                fields[name] = val
        return await commands.cal_update(
            ref_or_id=ref, source=source, ref_table=_refs, **fields,
        )
    return f"Error: unknown action '{action}'"
```

- [ ] **Step 2: Update test_server.py for tool registration**

Add to `tests/test_server.py` in the `TestToolRegistration` class:

```python
def test_cal_tool_registered(self):
    """cal tool is registered."""
    tool_names = [t.name for t in mcp.list_tools()]
    assert "cal" in tool_names

def test_cal_create_tool_registered(self):
    tool_names = [t.name for t in mcp.list_tools()]
    assert "cal_create" in tool_names

def test_cal_manage_tool_registered(self):
    tool_names = [t.name for t in mcp.list_tools()]
    assert "cal_manage" in tool_names
```

- [ ] **Step 3: Run server tests**

Run: `uv run pytest tests/test_server.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/ts4k/server.py tests/test_server.py
git commit -m "feat: add cal, cal_create, cal_manage MCP tools"
```

---

### Task 13: Update help and skill reference

**Files:**
- Modify: whatever help/skill files exist (check `src/ts4k/commands.py` for help text, and any skill reference files)

- [ ] **Step 1: Find and read the help and skill text locations**

Run: `grep -n "def.*help\|def.*skill\|HELP_TEXT\|SKILL_TEXT" src/ts4k/commands.py`

Check `docs/` for any skill reference files.

- [ ] **Step 2: Add calendar commands to help text**

Add to the help output:

```
Calendar:
  cal [today]         Today's events (default)
  cal tomorrow        Tomorrow's events
  cal week            This week (Mon-Sun)
  cal next [-n N]     Next N events (default 10)
  cal range --from DATE --to DATE
  cal event REF       Full event detail
  cal setup           Discover and add calendar sources
  cal create          Create event (--title, --start, --end, --source)
  cal update REF      Update event fields
  cal rsvp REF        RSVP (--status accepted/declined/tentative)
```

- [ ] **Step 3: Add calendar to skill/LLM help**

Add calendar guidance to the skill reference (if `ts4k skill` output exists):

```
Calendar: use `cal today` for today's agenda, `cal week` for the week.
Use `--source gc` to filter to one calendar. `cal setup` to add calendars.
Create events with `cal create --source gc --title "..." --start ... --end ...`.
RSVP with `cal rsvp REF --status accepted`. All dates are YYYY-MM-DD (inclusive for all-day).
```

- [ ] **Step 4: Commit**

```bash
git add src/ts4k/commands.py
git commit -m "docs: add calendar commands to help and skill reference"
```

---

### Task 14: Full integration test

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 2: CLI smoke test**

Run:
```bash
uv run ts4k cal --help
uv run ts4k cal setup --help
uv run ts4k cal create --help
```
Expected: Help text shows correctly for all subcommands

- [ ] **Step 3: Commit any final fixes, then version bump**

If all tests pass and CLI works:

```bash
# Bump version in pyproject.toml (patch bump)
git add -A
git commit -m "feat: Google Calendar adapter (Phase 6a) — complete"
```

---

## Summary

| Chunk | Tasks | What it delivers |
|-------|-------|-----------------|
| 1: Foundation | 1-3 | Gcal scopes, provider-aware check_level, build_calendar_service |
| 2: Adapter | 4-7 | Full GcalAdapter: list_events, read_event, list_calendars, create/update/rsvp |
| 3: Format | 8 | format_events, format_event_detail, adaptive time, recurring collapse |
| 4: Commands | 9-10 | Factory wiring, all cal_* command functions |
| 5: CLI + Auth | 11 | CLI subparser, setup wizard, auth integration |
| 6: MCP + Help | 12-14 | MCP tools, help text, full integration test |

Each chunk produces working, testable software. Chunks can be committed and tested independently.

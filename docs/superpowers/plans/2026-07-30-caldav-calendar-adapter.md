# CalDAV Calendar Adapter (Apple/iCloud) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a generic CalDAV calendar adapter (Apple/iCloud preset) so ts4k's `cal` commands work against iCloud alongside Google Calendar and O365 Calendar.

**Architecture:** New `CaldavAdapter` mirrors `GcalAdapter`'s surface and normalized event shape, built on the synchronous `caldav` PyPI library wrapped in `asyncio.to_thread`. Credentials are an Apple app-specific password stored as 0600 JSON under the config dir (no OAuth). Registration follows the existing pattern: a `provider` branch in `commands._make_adapter`, gate-list extension, and CLI `src add` support with `apple`/`icloud` aliases.

**Tech Stack:** Python 3.12+, `caldav` (new dep; pulls `icalendar`, `lxml`, `vobject`), pytest + pytest-asyncio (`asyncio_mode = "auto"` — no decorator needed on async tests).

**Spec:** `docs/superpowers/specs/2026-07-30-caldav-calendar-adapter-design.md`

## Global Constraints

- Python 3.12+, managed with `uv`. Run tests with `uv run pytest tests/<file> -v`.
- Only new dependency allowed: `caldav` (add via `uv add caldav`).
- Provider key: `caldav`. Default/suggested prefix: `cc`. Apple preset server URL: `https://caldav.icloud.com` (constant `ICLOUD_CALDAV_URL`).
- Normalized event dicts must use the **exact same keys** as `GcalAdapter._normalize_event` (`src/ts4k/adapters/gcal.py:178`): `id, source, title, start, end, all_day, duration_minutes, location, organizer, attendees_summary, status, your_status, recurring_event_id`.
- Match existing style: dataclass configs, `logger = logging.getLogger(__name__)`, `from __future__ import annotations`, level checks via `ts4k.core.levels.check_level`.
- Never log or print the app-specific password.
- Work on a feature branch (e.g. `feature/caldav-adapter`) created at execution time; commit after every green task.
- Test isolation: use the `ts4k_config` fixture from `tests/conftest.py` (sets `TS4K_CONFIG_DIR` + `state.set_config_dir`) whenever a test touches sources/state; plain `tmp_path` suffices for adapter/auth unit tests that take an explicit `config_dir`.

---

### Task 1: CalDAV credential storage

**Files:**
- Create: `src/ts4k/auth/caldav.py`
- Test: `tests/test_caldav_auth.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ICLOUD_CALDAV_URL: str`; `credentials_path(email: str, config_dir: Path | None = None) -> Path`; `save_credentials(email: str, *, username: str, app_password: str, server_url: str, config_dir: Path | None = None) -> Path`; `load_credentials(email: str, config_dir: Path | None = None) -> dict | None`. Later tasks (adapter `connect()`, CLI `src add`, `_token_health`) rely on these exact names.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for CalDAV credential storage."""

from __future__ import annotations

from pathlib import Path

from ts4k.auth.caldav import (
    ICLOUD_CALDAV_URL,
    credentials_path,
    load_credentials,
    save_credentials,
)


def test_path_layout(tmp_path: Path):
    p = credentials_path("a@icloud.com", config_dir=tmp_path)
    assert p == tmp_path / "caldav" / "a@icloud.com" / "credentials.json"


def test_save_and_load_roundtrip(tmp_path: Path):
    save_credentials(
        "a@icloud.com",
        username="a@icloud.com",
        app_password="abcd-efgh-ijkl-mnop",
        server_url=ICLOUD_CALDAV_URL,
        config_dir=tmp_path,
    )
    creds = load_credentials("a@icloud.com", config_dir=tmp_path)
    assert creds == {
        "username": "a@icloud.com",
        "app_password": "abcd-efgh-ijkl-mnop",
        "server_url": ICLOUD_CALDAV_URL,
    }


def test_file_permissions_0600(tmp_path: Path):
    path = save_credentials(
        "a@icloud.com",
        username="a@icloud.com",
        app_password="x",
        server_url=ICLOUD_CALDAV_URL,
        config_dir=tmp_path,
    )
    assert (path.stat().st_mode & 0o777) == 0o600


def test_load_missing_returns_none(tmp_path: Path):
    assert load_credentials("nobody@icloud.com", config_dir=tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_caldav_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ts4k.auth.caldav'`

- [ ] **Step 3: Write the implementation**

```python
"""CalDAV credential storage — Apple app-specific passwords, no OAuth.

Credentials live at ``~/.config/ts4k/caldav/<email>/credentials.json``
(0600).  Generate an app-specific password at https://account.apple.com
(Sign-In and Security → App-Specific Passwords; requires 2FA).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ICLOUD_CALDAV_URL = "https://caldav.icloud.com"


def _default_config_dir() -> Path:
    """Resolve auth config dir: env var → global default (mirrors auth/google.py)."""
    env = os.environ.get("TS4K_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "ts4k"


def credentials_path(email: str, config_dir: Path | None = None) -> Path:
    base = Path(config_dir) if config_dir else _default_config_dir()
    return base / "caldav" / email / "credentials.json"


def save_credentials(
    email: str,
    *,
    username: str,
    app_password: str,
    server_url: str,
    config_dir: Path | None = None,
) -> Path:
    path = credentials_path(email, config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"username": username, "app_password": app_password, "server_url": server_url},
        indent=2,
    ))
    path.chmod(0o600)
    return path


def load_credentials(email: str, config_dir: Path | None = None) -> dict | None:
    path = credentials_path(email, config_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_caldav_auth.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/auth/caldav.py tests/test_caldav_auth.py
git commit -m "feat: CalDAV credential storage (app-specific password, 0600 JSON)"
```

---

### Task 2: Adapter skeleton — config, connect, stubs, level gates

**Files:**
- Create: `src/ts4k/adapters/caldav_cal.py`
- Modify: `pyproject.toml` (via `uv add caldav`)
- Test: `tests/test_caldav_adapter.py`

**Interfaces:**
- Consumes: `load_credentials` from Task 1; `BaseAdapter` (`src/ts4k/adapters/base.py`); `AccessLevel, check_level, parse_level` from `ts4k.core.levels`.
- Produces: `CaldavAdapterConfig(email, server_url, calendar_id, calendar_name="", timezone="UTC", config_dir=None, level="readonly")` and `CaldavAdapter(config, prefix="cc")` with `connect/disconnect/__aenter__/__aexit__`, message stubs, `_check_modify/_check_draft/_check_send`, `_strip_prefix`, and `self._client/self._principal/self._calendar` attributes that Tasks 3–6 build on. `_get_calendar()` returns the resolved `caldav.Calendar` for `config.calendar_id`.

- [ ] **Step 1: Add the dependency**

Run: `uv add caldav`
Expected: `caldav` appears in `[project] dependencies` in `pyproject.toml`; `uv run python -c "import caldav; print(caldav.__version__)"` prints a version.

- [ ] **Step 2: Write the failing tests**

```python
"""Tests for the CalDAV calendar adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_caldav_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ts4k.adapters.caldav_cal'`

- [ ] **Step 4: Write the implementation**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_caldav_adapter.py -v`
Expected: all pass

- [ ] **Step 6: Run the full suite to catch regressions**

Run: `uv run pytest`
Expected: no new failures

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/ts4k/adapters/caldav_cal.py tests/test_caldav_adapter.py
git commit -m "feat: CalDAV adapter skeleton — config, connect, stubs, level gates"
```

---

### Task 3: VEVENT normalization + list_events

**Files:**
- Modify: `src/ts4k/adapters/caldav_cal.py`
- Test: `tests/test_caldav_adapter.py` (append)

**Interfaces:**
- Consumes: `_get_calendar()`, `self._config.timezone`, `self._prefix` from Task 2. The `caldav` calendar object's `search(start=datetime, end=datetime, event=True, expand=True)` returning objects with an `.icalendar_component` property (an `icalendar` VEVENT).
- Produces: `_normalize_component(comp) -> dict` (gcal-shaped dict; instance IDs are `uid::<recurrence-id-iso>`, master IDs are bare `uid`) and `async list_events(time_min: str, time_max: str, count: int = 250) -> list[dict]`. Tasks 4–7 reuse `_normalize_component`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_caldav_adapter.py`)

```python
from icalendar import Calendar as IcsCalendar


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_caldav_adapter.py -v`
Expected: new tests FAIL with `AttributeError: ... has no attribute '_normalize_component'`

- [ ] **Step 3: Write the implementation** (add to `CaldavAdapter`, plus module-level map)

```python
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
```

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_caldav_adapter.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/adapters/caldav_cal.py tests/test_caldav_adapter.py
git commit -m "feat: CalDAV VEVENT normalization and list_events with expansion"
```

---

### Task 4: read_event + list_calendars

**Files:**
- Modify: `src/ts4k/adapters/caldav_cal.py`
- Test: `tests/test_caldav_adapter.py` (append)

**Interfaces:**
- Consumes: `_normalize_component`, `_get_calendar`, `_strip_prefix` from Tasks 2–3; `rrule_to_human` from `ts4k.adapters.gcal` (module-level at `gcal.py:23`). The caldav calendar's `event_by_uid(uid)` method (fallback `object_by_uid(uid)` if the installed version lacks the alias — check at implementation time with `uv run python -c "import caldav; print(hasattr(caldav.Calendar, 'event_by_uid'))"`).
- Produces: `async read_event(event_id: str) -> dict` (base dict + `attendees` (list of `{name,email,status}`), `description`, `meeting_link`, `recurrence`, `recurrence_summary`, `created`, `updated` — matching gcal's `read_event` extras at `gcal.py:240`); `async list_calendars() -> list[dict]` with keys `id, summary, access_role, timezone, primary` (matching `gcal.py:284`).

- [ ] **Step 1: Write the failing tests** (append)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_caldav_adapter.py -v`
Expected: new tests FAIL (`read_event`/`list_calendars` missing — note the base class has no default; AttributeError or TypeError acceptable)

- [ ] **Step 3: Write the implementation** (add to `CaldavAdapter`)

```python
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
```

Note: `RRULE:FREQ=WEEKLY;BYDAY=TH` may serialize from icalendar as `FREQ=WEEKLY;BYDAY=TH` — if the test fails on key order (e.g. `BYDAY=TH;FREQ=WEEKLY`), relax the assertion to check both parts are present, and pass the same string through `rrule_to_human` in the assertion instead of hardcoding.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_caldav_adapter.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/adapters/caldav_cal.py tests/test_caldav_adapter.py
git commit -m "feat: CalDAV read_event and list_calendars"
```

---

### Task 5: create_event + update_event (level-gated)

**Files:**
- Modify: `src/ts4k/adapters/caldav_cal.py`
- Test: `tests/test_caldav_write.py` (new file, mirrors `tests/test_gcal_write.py` structure)

**Interfaces:**
- Consumes: `_check_draft/_check_send/_check_modify`, `_get_calendar`, `_fetch_by_uid`, `_normalize_component` from earlier tasks; caldav calendar's `save_event(ics_string)` returning the saved object; caldav object's `save()`.
- Produces: `async create_event(title, start, end, description=None, location=None, attendees=None) -> dict` and `async update_event(event_id, **fields) -> dict` accepting `title, description, location, start, end` — same signatures as `gcal.py:313` and `gcal.py:358` (Task 7's `cal_create`/`cal_update` call these polymorphically).

- [ ] **Step 1: Write the failing tests**

```python
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
        a = _adapter(tmp_path, "modify")
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
        a = _adapter(tmp_path, "modify")
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
```

Note on `PermissionError`: before writing these tests, check what `check_level` raises (`src/ts4k/core/levels.py:38`) — if it raises a different exception type (e.g. a custom error), assert that type instead. Mirror whatever `tests/test_gcal_write.py` / `tests/test_o365cal_levels.py` assert.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_caldav_write.py -v`
Expected: FAIL (`create_event` not defined on `CaldavAdapter`; gating tests fail with AttributeError, not PermissionError — that's expected at this stage)

- [ ] **Step 3: Write the implementation** (add to `CaldavAdapter`)

```python
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

        import uuid

        from icalendar import Calendar as IcsCalendar
        from icalendar import Event as IcsEvent
        from icalendar import vCalAddress

        vevent = IcsEvent()
        vevent.add("UID", str(uuid.uuid4()))
        vevent.add("SUMMARY", title)
        vevent.add("DTSTAMP", datetime.now(timezone.utc))

        tzinfo = self._tzinfo()
        if "T" not in start:
            # All-day: user provides inclusive end, iCal DTEND is exclusive
            end_date = date.fromisoformat(end) + timedelta(days=1)
            vevent.add("DTSTART", date.fromisoformat(start))
            vevent.add("DTEND", end_date)
        else:
            start_dt = datetime.fromisoformat(start)
            end_dt = datetime.fromisoformat(end)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=tzinfo)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=tzinfo)
            vevent.add("DTSTART", start_dt)
            vevent.add("DTEND", end_dt)

        if description:
            vevent.add("DESCRIPTION", description)
        if location:
            vevent.add("LOCATION", location)
        for email in attendees or []:
            att = vCalAddress(f"mailto:{email}")
            att.params["PARTSTAT"] = "NEEDS-ACTION"
            att.params["ROLE"] = "REQ-PARTICIPANT"
            vevent.add("ATTENDEE", att, encode=0)

        ics = IcsCalendar()
        ics.add("VERSION", "2.0")
        ics.add("PRODID", "-//ts4k//caldav//EN")
        ics.add_component(vevent)

        cal = await self._get_calendar()
        saved = await asyncio.to_thread(
            lambda: cal.save_event(ics.to_ical().decode())
        )
        return self._normalize_component(saved.icalendar_component)

    async def update_event(self, event_id: str, **fields: Any) -> dict:
        """Update an existing event. Requires MODIFY level."""
        self._check_modify("update_event")
        raw = self._strip_prefix(event_id)
        uid = raw.split("::")[0]
        obj = await self._fetch_by_uid(uid)
        comp = obj.icalendar_component
        tzinfo = self._tzinfo()

        def _set(key: str, value: Any) -> None:
            comp.pop(key, None)
            comp.add(key, value)

        if "title" in fields:
            _set("SUMMARY", fields["title"])
        if "description" in fields:
            _set("DESCRIPTION", fields["description"])
        if "location" in fields:
            _set("LOCATION", fields["location"])
        if "start" in fields:
            s = fields["start"]
            if "T" not in s:
                _set("DTSTART", date.fromisoformat(s))
            else:
                dt = datetime.fromisoformat(s)
                _set("DTSTART", dt.replace(tzinfo=tzinfo) if dt.tzinfo is None else dt)
        if "end" in fields:
            e = fields["end"]
            if "T" not in e:
                _set("DTEND", date.fromisoformat(e))
            else:
                dt = datetime.fromisoformat(e)
                _set("DTEND", dt.replace(tzinfo=tzinfo) if dt.tzinfo is None else dt)

        await asyncio.to_thread(obj.save)
        return self._normalize_component(comp)
```

(The `rsvp` gating test stays red until Task 6 — if running tests file-wide here, add the minimal `async def rsvp(self, event_id: str, status: str) -> dict: self._check_modify("rsvp"); raise NotImplementedError` stub now and replace it in Task 6.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_caldav_write.py -v`
Expected: all pass (with the `rsvp` stub in place for the gating test)

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/adapters/caldav_cal.py tests/test_caldav_write.py
git commit -m "feat: CalDAV create_event and update_event with level gating"
```

---

### Task 6: rsvp with graceful degradation

**Files:**
- Modify: `src/ts4k/adapters/caldav_cal.py` (replace Task 5's stub)
- Modify: `src/ts4k/commands.py` — `cal_rsvp` (~line 2385): wrap the adapter call so clean errors return as strings
- Test: `tests/test_caldav_write.py` (append)

**Interfaces:**
- Consumes: `_fetch_by_uid`, `_normalize_component`, `_check_modify`, `_PARTSTAT_MAP`, `_strip_mailto`.
- Produces: `async rsvp(event_id: str, status: str) -> dict` where `status ∈ {"accepted", "declined", "tentative"}` (the values `cal rsvp --status` passes). Raises `ValueError` (bad status / not an attendee) or `RuntimeError` (server rejected) with actionable messages. `cal_rsvp` catches both and returns `f"Error: {e}"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_caldav_write.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_caldav_write.py -v`
Expected: new tests FAIL (stub raises NotImplementedError)

- [ ] **Step 3: Write the implementation** (replace the stub)

```python
    async def rsvp(self, event_id: str, status: str) -> dict:
        """RSVP to an event. Requires MODIFY level.

        Prefers the caldav library's scheduling helpers (accept_invite etc.)
        when present; falls back to editing PARTSTAT directly.  iCloud often
        rejects scheduling writes on externally-organized invites — surfaced
        as a clean RuntimeError, never a stack trace.
        """
        self._check_modify("rsvp")

        helper_names = {
            "accepted": "accept_invite",
            "declined": "decline_invite",
            "tentative": "tentatively_accept_invite",
        }
        partstat_values = {
            "accepted": "ACCEPTED",
            "declined": "DECLINED",
            "tentative": "TENTATIVE",
        }
        if status not in partstat_values:
            raise ValueError(
                f"Invalid RSVP status {status!r} — use accepted, declined, or tentative"
            )

        raw = self._strip_prefix(event_id)
        uid = raw.split("::")[0]
        obj = await self._fetch_by_uid(uid)
        comp = obj.icalendar_component

        my_email = self._config.email.lower()
        raw_attendees = comp.get("ATTENDEE")
        if raw_attendees is None:
            raw_attendees = []
        elif not isinstance(raw_attendees, list):
            raw_attendees = [raw_attendees]
        me = next(
            (a for a in raw_attendees if _strip_mailto(a).lower() == my_email), None
        )
        if me is None:
            raise ValueError(
                f"{self._config.email} is not an attendee on this event — "
                f"cannot RSVP"
            )

        helper = getattr(obj, helper_names[status], None)
        if callable(helper):
            try:
                await asyncio.to_thread(helper)
                return self._normalize_component(comp)
            except Exception:
                logger.info("caldav invite helper failed; falling back to PARTSTAT edit")

        me.params["PARTSTAT"] = partstat_values[status]
        try:
            await asyncio.to_thread(obj.save)
        except Exception as e:
            raise RuntimeError(
                f"RSVP not accepted by the server ({e}) — iCloud often blocks "
                f"programmatic replies to external invites; respond in the "
                f"Calendar app instead"
            ) from e
        return self._normalize_component(comp)
```

Mock-interaction note: with a bare `MagicMock` object, `getattr(obj, "accept_invite")` is auto-created and callable — that's why the helper-path test uses a plain MagicMock and the manual-path test uses `spec=["icalendar_component", "save"]` to make the helper absent. After the helper succeeds we normalize the *unmodified* component, so `your_status` in that test would still be `needsAction` — the test deliberately only asserts the helper was called, not the returned status.

- [ ] **Step 4: Wrap cal_rsvp errors in commands.py**

In `src/ts4k/commands.py`, `cal_rsvp` currently ends with:

```python
    async with adapter:
        event = await adapter.rsvp(event_id, status=status)

    return f"RSVP {status}: {event['title']} ({event['id']})"
```

Change to:

```python
    try:
        async with adapter:
            event = await adapter.rsvp(event_id, status=status)
    except (ValueError, RuntimeError) as e:
        return f"Error: {e}"

    return f"RSVP {status}: {event['title']} ({event['id']})"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_caldav_write.py tests/test_cal_commands.py -v`
Expected: all pass, no regressions in cal command tests

- [ ] **Step 6: Commit**

```bash
git add src/ts4k/adapters/caldav_cal.py src/ts4k/commands.py tests/test_caldav_write.py
git commit -m "feat: CalDAV rsvp with invite-helper preference and clean fallback errors"
```

---

### Task 7: Register provider in commands.py

**Files:**
- Modify: `src/ts4k/commands.py` — imports, `_make_adapter` (~line 74), `_resolve_prefixes` provider_map (~line 172), `_provider_labels` (~line 901), stats skip tuple (~line 917), `_token_health` (~line 1930), all `("gcal", "o365cal")` gate tuples (~lines 2149, 2194, 2289, 2338, 2366, 2391 and `_get_cal_timezone`), new `cal_list_caldav_calendars`
- Test: `tests/test_caldav_commands.py`

**Interfaces:**
- Consumes: `CaldavAdapter`, `CaldavAdapterConfig` (Task 2), `ICLOUD_CALDAV_URL`, `load_credentials` (Task 1).
- Produces: `_make_adapter` builds `CaldavAdapter` for `provider == "caldav"`; module constant `_CAL_PROVIDERS = ("gcal", "o365cal", "caldav")`; `async cal_list_caldav_calendars(email: str, config_dir: Path | None = None) -> list[dict]` (Task 8's CLI calls this exact name).

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for CalDAV provider registration in commands.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ts4k import commands
from ts4k.adapters.caldav_cal import CaldavAdapter

CALDAV_CFG = {
    "provider": "caldav",
    "email": "a@icloud.com",
    "server_url": "https://caldav.icloud.com",
    "calendar_id": "https://caldav.icloud.com/123/calendars/home/",
    "calendar_name": "Home",
    "timezone": "UTC",
    "level": "modify",
}


class TestMakeAdapter:
    def test_builds_caldav_adapter(self):
        a = commands._make_adapter("cc", dict(CALDAV_CFG))
        assert isinstance(a, CaldavAdapter)
        assert a.source_prefix == "cc"

    def test_missing_email_returns_none(self):
        cfg = dict(CALDAV_CFG)
        del cfg["email"]
        assert commands._make_adapter("cc", cfg) is None

    def test_missing_calendar_id_returns_none(self):
        cfg = dict(CALDAV_CFG)
        del cfg["calendar_id"]
        assert commands._make_adapter("cc", cfg) is None


class TestResolvePrefixes:
    def test_apple_alias_resolves_to_caldav_sources(self, monkeypatch):
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"cc": dict(CALDAV_CFG)}
        )
        assert commands._resolve_prefixes("apple") == ["cc"]
        assert commands._resolve_prefixes("icloud") == ["cc"]
        assert commands._resolve_prefixes("caldav") == ["cc"]


class TestCalGates:
    async def test_cal_create_accepts_caldav_source(self, monkeypatch):
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"cc": dict(CALDAV_CFG)}
        )
        fake = MagicMock()
        fake.__aenter__ = AsyncMock(return_value=fake)
        fake.__aexit__ = AsyncMock(return_value=None)
        fake.create_event = AsyncMock(return_value={"title": "X", "id": "cc:1"})
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: fake)
        out = await commands.cal_create(
            "cc", "X", "2026-07-30T10:00:00", "2026-07-30T11:00:00",
            None, None, None,
        )
        assert out == "Created: X (cc:1)"


class TestTokenHealth:
    def test_caldav_with_credentials_is_ok(self, tmp_path: Path, monkeypatch):
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL, save_credentials

        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        save_credentials("a@icloud.com", username="a@icloud.com",
                         app_password="x", server_url=ICLOUD_CALDAV_URL)
        th = commands._token_health(dict(CALDAV_CFG))
        assert th.status == "ok"

    def test_caldav_without_credentials_is_na(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        th = commands._token_health(dict(CALDAV_CFG))
        assert th.status == "na"
```

Note: `_token_health` is the private helper around `commands.py:1930` that returns `TokenHealth` — confirm its exact name with `grep -n "def _token_health\|TokenHealth(" src/ts4k/commands.py` before writing the test, and use whatever it's actually called.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_caldav_commands.py -v`
Expected: FAIL — `_make_adapter` logs "Unknown provider 'caldav'" and returns None

- [ ] **Step 3: Implement the registration changes**

In `src/ts4k/commands.py`:

1. Add imports next to the existing adapter imports (~line 21):

```python
from ts4k.adapters.caldav_cal import CaldavAdapter, CaldavAdapterConfig
```

2. Define the gate constant near the other module-level helpers (above `_make_adapter`):

```python
_CAL_PROVIDERS = ("gcal", "o365cal", "caldav")
```

3. Add the factory branch in `_make_adapter` (after the `o365cal` branch), and add `CaldavAdapter` to the return-type union:

```python
    if provider == "caldav":
        email = cfg.get("email")
        calendar_id = cfg.get("calendar_id")
        if not email or not calendar_id:
            return None
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL
        config = CaldavAdapterConfig(
            email=email,
            server_url=cfg.get("server_url", ICLOUD_CALDAV_URL),
            calendar_id=calendar_id,
            calendar_name=cfg.get("calendar_name", ""),
            timezone=cfg.get("timezone", "UTC"),
            config_dir=Path(cfg["config_dir"]) if cfg.get("config_dir") else None,
            level=cfg.get("level", "readonly"),
        )
        return CaldavAdapter(config, prefix=prefix)
```

4. Extend the provider alias map in `_resolve_prefixes`:

```python
        "apple": "caldav", "icloud": "caldav", "apple-calendar": "caldav",
```

5. `_provider_labels` (~line 901): add `"caldav": "CalDAV"`.

6. Replace every `("gcal", "o365cal")` tuple in commands.py with `_CAL_PROVIDERS` — find them all with `grep -n '"gcal", "o365cal"' src/ts4k/commands.py` (expect ~7: stats skip, event-fetch filter, `_get_cal_timezone`, `cal_read`, `cal_create`, `cal_update`, `cal_rsvp`).

7. Add a `caldav` branch to the token-health helper (before the final unknown-provider return):

```python
    if provider == "caldav":
        from ts4k.auth.caldav import load_credentials
        email = cfg.get("email", "")
        if not email:
            return TokenHealth(status="na", expiry=None, scopes=[], detail="no email configured")
        if load_credentials(email) is None:
            return TokenHealth(status="na", expiry=None, scopes=[],
                               detail="no credentials — run: ts4k src add <prefix> apple email=" + email)
        return TokenHealth(status="ok", expiry=None, scopes=[], detail="app-specific password")
```

8. Add the setup helper next to `cal_list_calendars` / `cal_list_o365_calendars` (~line 2303):

```python
async def cal_list_caldav_calendars(
    email: str, config_dir: Path | None = None,
) -> list[dict]:
    """List available calendars for a CalDAV account (non-interactive, for setup)."""
    from ts4k.auth.caldav import ICLOUD_CALDAV_URL

    config = CaldavAdapterConfig(
        email=email, server_url=ICLOUD_CALDAV_URL, calendar_id="",
        calendar_name="", timezone="UTC", config_dir=config_dir,
        level="readonly",
    )
    adapter = CaldavAdapter(config, prefix="_setup")
    async with adapter:
        return await adapter.list_calendars()
```

(`connect()` prefers the `server_url` stored in the credentials file, so the iCloud default here is only a fallback.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_caldav_commands.py -v`
Expected: all pass

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: no regressions (the `_CAL_PROVIDERS` sweep touches shared code paths)

- [ ] **Step 6: Commit**

```bash
git add src/ts4k/commands.py tests/test_caldav_commands.py
git commit -m "feat: register caldav provider — factory, gates, aliases, token health"
```

---

### Task 8: CLI `src add` support with Apple preset

**Files:**
- Modify: `src/ts4k/cli.py` — `_cmd_sources` add-branch (~line 257), `_suggest_cal_prefix` (~line 844), `sr_add` parser epilog (~line 1203)
- Test: `tests/test_caldav_cli.py`

**Interfaces:**
- Consumes: `commands.cal_list_caldav_calendars` (Task 7), `save_credentials`/`load_credentials`/`ICLOUD_CALDAV_URL` (Task 1), `sources.add`, `_suggest_cal_prefix`.
- Produces: `ts4k src add <prefix> apple email=you@icloud.com` — prompts for the app-specific password (getpass) if credentials are missing, validates by listing calendars, then interactive calendar selection; first selected calendar gets `<prefix>`, others get suggested `cc*` prefixes. `calendar_id=...` in params skips the picker and adds directly.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for CalDAV source-add CLI flow."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, patch

from ts4k import cli
from ts4k.state import sources


def _args(provider: str, params: list[str]) -> argparse.Namespace:
    return argparse.Namespace(
        action="add", prefix="cc", provider=provider, params=params,
    )


CALS = [
    {"id": "https://caldav.icloud.com/1/calendars/home/", "summary": "Home",
     "access_role": "owner", "timezone": "UTC", "primary": False},
    {"id": "https://caldav.icloud.com/1/calendars/work/", "summary": "Work",
     "access_role": "owner", "timezone": "UTC", "primary": False},
]


class TestSrcAddApple:
    def test_apple_alias_prompts_saves_and_adds_selected(self, ts4k_config, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "abcd-efgh")
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(return_value=CALS)):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        from ts4k.auth.caldav import load_credentials
        creds = load_credentials("a@icloud.com")
        assert creds is not None and creds["app_password"] == "abcd-efgh"
        assert creds["server_url"] == "https://caldav.icloud.com"

        cfg = sources.list_all()["cc"]
        assert cfg["provider"] == "caldav"
        assert cfg["calendar_id"] == "https://caldav.icloud.com/1/calendars/home/"
        assert cfg["calendar_name"] == "Home"
        assert cfg["level"] == "readonly"

    def test_explicit_calendar_id_skips_picker(self, ts4k_config, monkeypatch):
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL, save_credentials

        save_credentials("a@icloud.com", username="a@icloud.com",
                         app_password="x", server_url=ICLOUD_CALDAV_URL)
        cli._cmd_sources(_args("apple", [
            "email=a@icloud.com",
            "calendar_id=https://caldav.icloud.com/1/calendars/home/",
            "calendar_name=Home",
        ]))
        cfg = sources.list_all()["cc"]
        assert cfg["provider"] == "caldav"
        assert cfg["calendar_id"] == "https://caldav.icloud.com/1/calendars/home/"

    def test_missing_email_prints_usage_and_adds_nothing(self, ts4k_config, capsys):
        cli._cmd_sources(_args("apple", []))
        assert "email" in capsys.readouterr().out
        assert "cc" not in sources.list_all()


class TestSuggestPrefix:
    def test_caldav_base_is_cc(self):
        assert cli._suggest_cal_prefix("Home", {}, provider="caldav").startswith("cc")
```

Isolation caveat: `sources.py` computes `_CONFIG_DIR` at import time from `TS4K_CONFIG_DIR`. The `ts4k_config` fixture calls `state.set_config_dir` to repoint state modules — confirm `sources.list_all()` respects it by checking how existing tests (e.g. `tests/test_commands.py` or `tests/test_auth_cli.py`) isolate sources, and mirror that mechanism exactly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_caldav_cli.py -v`
Expected: FAIL — the apple alias isn't recognized (falls through to generic `sources.add` with provider "apple", so no credential prompt / no caldav entry)

- [ ] **Step 3: Implement the CLI flow**

In `src/ts4k/cli.py`, inside `_cmd_sources` after the params-parsing loop and before the O365 block, add:

```python
        # Apple/iCloud preset → generic caldav provider
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL
        _CALDAV_ALIASES = {"apple": ICLOUD_CALDAV_URL, "icloud": ICLOUD_CALDAV_URL,
                           "apple-calendar": ICLOUD_CALDAV_URL}
        if provider in _CALDAV_ALIASES:
            kwargs.setdefault("server_url", _CALDAV_ALIASES[provider])
            provider = "caldav"

        if provider == "caldav":
            email = kwargs.get("email")
            if not email:
                print("Error: email is required for CalDAV sources.")
                print(f"Usage: ts4k src add {prefix} apple email=you@icloud.com")
                return
            kwargs.setdefault("server_url", ICLOUD_CALDAV_URL)

            from ts4k.auth.caldav import load_credentials, save_credentials
            if load_credentials(email) is None:
                import getpass
                print("An app-specific password is required "
                      "(https://account.apple.com → Sign-In and Security → "
                      "App-Specific Passwords; needs 2FA).")
                pw = getpass.getpass(f"App-specific password for {email}: ")
                if not pw:
                    print("No password entered — aborting.")
                    return
                save_credentials(email, username=email, app_password=pw,
                                 server_url=kwargs["server_url"])
                print(f"Saved credentials for {email}.")

            if "calendar_id" not in kwargs:
                print(f"Fetching calendars for {email}...")
                try:
                    cals = asyncio.run(commands.cal_list_caldav_calendars(email))
                except Exception as e:
                    print(f"Error: could not list calendars — {e}")
                    return
                if not cals:
                    print("No calendars found.")
                    return
                for i, cal in enumerate(cals, 1):
                    print(f"  {i}. {cal['summary']}")
                choice = input("Which calendars? (comma-separated, or 'all'): ").strip()
                if choice.lower() == "all":
                    selected = cals
                else:
                    indices = [int(i.strip()) - 1
                               for i in choice.split(",") if i.strip().isdigit()]
                    selected = [cals[i] for i in indices if 0 <= i < len(cals)]
                if not selected:
                    print("No calendars selected.")
                    return
                all_sources = sources.list_all()
                for n, cal in enumerate(selected):
                    if n == 0 and prefix not in all_sources:
                        pfx = prefix
                    else:
                        suggested = _suggest_cal_prefix(cal["summary"], all_sources,
                                                        provider="caldav")
                        pfx = input(f"Prefix for '{cal['summary']}'? [{suggested}]: ").strip() or suggested
                    if pfx in all_sources:
                        print(f"  Prefix '{pfx}' already in use — skipping.")
                        continue
                    sources.add(
                        pfx, provider="caldav", email=email,
                        server_url=kwargs["server_url"],
                        calendar_id=cal["id"], calendar_name=cal["summary"],
                        timezone=cal.get("timezone", "UTC"), level="readonly",
                    )
                    all_sources[pfx] = {}
                    print(f"  Added '{cal['summary']}' as '{pfx}' (readonly)")
                return
            # calendar_id given explicitly → fall through to generic sources.add
```

Then:

1. `_suggest_cal_prefix` (~line 844): change `base = "oc" if provider == "o365cal" else "gc"` to:

```python
    base = {"o365cal": "oc", "caldav": "cc"}.get(provider, "gc")
```

2. `sr_add` parser (~line 1203): add to the epilog provider keys — `"  apple/icloud: email (required), calendar_id, calendar_name  → generic caldav provider"` — and an example line `'  ts4k src add cc apple email=you@icloud.com'`; update the provider argument help to `"Provider: gmail, o365, whatsapp, apple/icloud/caldav"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_caldav_cli.py -v`
Expected: all pass

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: no regressions

- [ ] **Step 6: Commit**

```bash
git add src/ts4k/cli.py tests/test_caldav_cli.py
git commit -m "feat: ts4k src add apple — credential prompt and calendar picker for caldav"
```

---

### Task 9: Documentation + manual smoke test

**Files:**
- Modify: `README.md` (calendar/sources setup section — locate it first; follow its existing structure)
- Create: nothing else

**Interfaces:** none — docs and verification only.

- [ ] **Step 1: Add README section**

Find the calendar setup docs in `README.md` (`grep -n -i "calendar\|src add" README.md`) and add an Apple/iCloud subsection styled like the neighbors:

```markdown
### Apple / iCloud Calendar (CalDAV)

iCloud calendars connect over CalDAV with an app-specific password — no OAuth setup.

1. Generate an app-specific password at https://account.apple.com
   (Sign-In and Security → App-Specific Passwords; requires 2FA).
2. Add the source and pick calendars interactively:

    ts4k src add cc apple email=you@icloud.com

The same provider works for any CalDAV server (Fastmail, Nextcloud, ...):
pass `server_url=https://your-server/` instead of the `apple` preset.

Notes:
- Write access needs `level=modify` (`ts4k src add cc apple email=... level=modify`,
  or edit the source after adding).
- RSVP is best-effort: iCloud often blocks programmatic replies to external
  invites — ts4k reports this cleanly and you respond in the Calendar app.
- Free-text event search is filtered client-side (CalDAV limitation).
```

- [ ] **Step 2: Run the full suite one final time**

Run: `uv run pytest`
Expected: everything green

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: Apple/iCloud CalDAV calendar setup"
```

- [ ] **Step 4: Manual smoke test against real iCloud (needs Peter's credentials — coordinate with user)**

This step requires a real app-specific password and cannot be automated. Walk through with the user:

1. `ts4k src add cc apple email=<apple-id>` → password prompt → calendar list appears → pick one.
2. `ts4k src list` → the `cc` source shows provider caldav, level readonly.
3. `ts4k cal today` (or the equivalent agenda command) → events from iCloud appear alongside gcal/o365cal sources.
4. `ts4k cal get <ref>` → full detail renders (attendees, recurrence summary).
5. Set `level=modify` on the source, then `ts4k cal create ...` → event appears in the Calendar app.
6. `ts4k cal update ...` on that event → change syncs.
7. `ts4k cal rsvp <ref> --status accepted` on a real invite → either succeeds or prints the clean "respond in the Calendar app" error (both are acceptable outcomes; a stack trace is a failure).
8. `ts4k status` → the caldav source shows health "ok / app-specific password".

Record any API-shape surprises (e.g. `event_by_uid` missing on the installed caldav version) and fix within the adapter, keeping tests green.

---

## Self-Review Notes

- **Spec coverage:** provider shape/aliases (T7, T8), full surface incl. RSVP (T3–T6), credentials 0600 + interactive prompt (T1, T8), caldav library + to_thread (T2+), normalization parity incl. instance expansion (T3), levels as local gates (T2, T5; `scopes_for` already returns `[]` for unknown providers — no levels.py change needed, verified against `src/ts4k/core/levels.py:120`), actionable auth errors (T2), RSVP degradation (T6), client-side text search (no code change needed — text filtering doesn't pass through adapters' `list_events`; documented in README, T9), platform isolation (existing per-adapter error handling in command layer, unchanged), README docs + smoke test (T9).
- **Known API-risk points** (all caught by the smoke test, all localized to the adapter): `Calendar.event_by_uid` alias availability, `search(expand=True)` client-side fallback behavior, `DAVClient.close()` availability. Where a check is cheap, the task text says to verify at implementation time.
- Exception type for level violations must be confirmed in Task 5 Step 1 (mirror `tests/test_o365cal_levels.py`).

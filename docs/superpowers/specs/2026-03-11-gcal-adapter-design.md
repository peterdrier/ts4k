# Google Calendar Adapter — Design Spec (Phase 6a)

## Overview

Add a Google Calendar adapter to ts4k, enabling token-efficient calendar event retrieval and management via CLI, MCP, and Skill interfaces. This is Phase 6a from the project roadmap.

ts4k's calendar support follows the same principles as messaging: retrieve, normalize, and deliver. The adapter reads events from Google Calendar API, normalizes them into a compact format, and presents them through the existing command/format pipeline. Write operations (create, update, RSVP, invite) are gated by access levels.

## Source Model

One source per calendar. Each calendar gets its own prefix in `sources.json`, its own access level, and appears as a distinct source in all calendar commands.

### Setup Wizard

`ts4k cal setup` discovers calendars across all configured Google accounts. The interactive selection logic lives in `cli.py`; `commands.py` provides a non-interactive `cal_list_calendars(email)` that returns available calendars as data.

```
$ ts4k cal setup
Found Google account: peter@gmail.com (from source 'g')
Found Google account: peter@work.com (from source 'gw')

Fetching calendars for peter@gmail.com...
  1. peter@gmail.com (primary)
  2. Family
  3. Birthdays
  4. Holidays in Netherlands

Fetching calendars for peter@work.com...
  5. peter@work.com (primary)
  6. Team Standups

Which calendars? (comma-separated, or 'all'): 1,2,5
Prefix for 'peter@gmail.com'? [gc]:
Prefix for 'Family'? [gcf]:
Prefix for 'peter@work.com'? [gcw]:

Added 3 calendar sources (readonly).
```

The wizard:
- Scans `sources.json` for existing Gmail sources to find Google accounts
- Uses `get_credentials()` with `calendar.readonly` scope (triggers re-auth if needed)
- Calls `calendarList.list` to enumerate available calendars (follows `nextPageToken` for accounts with many calendars)
- Filters out `freeBusyReader`-only calendars (insufficient access for event details)
- Suggests prefixes based on calendar name (user can override)
- Rejects duplicate prefixes (prefix already exists in `sources.json`)
- Skips calendars already configured (same `calendar_id` + `email` combination)
- Saves the calendar's `timeZone` from the API response into the source config
- Creates one `sources.json` entry per selected calendar
- Defaults to `readonly` level

### Source Configuration

```json
{
  "gc": {
    "provider": "gcal",
    "email": "peter@gmail.com",
    "calendar_id": "primary",
    "calendar_name": "peter@gmail.com",
    "timezone": "Europe/Amsterdam",
    "level": "readonly"
  },
  "gcf": {
    "provider": "gcal",
    "email": "peter@gmail.com",
    "calendar_id": "family123@group.calendar.google.com",
    "calendar_name": "Family",
    "timezone": "Europe/Amsterdam",
    "level": "readonly"
  }
}
```

Multiple calendar sources sharing the same `email` share the same OAuth token. `get_credentials()` handles scope expansion transparently — if a calendar source is added to an account that previously only had Gmail, the user re-auths once to add the calendar scope.

### Isolation from Messaging Commands

Calendar sources use the `"gcal"` provider. Messaging commands (`whatsnew`, `list`, `get`, `thread`) resolve sources via `_resolve_prefixes()`. To prevent gcal sources from being included in `--source all` messaging queries (which would crash since gcal adapters don't implement messaging methods), the messaging abstract methods on `GcalAdapter` return empty results instead of raising:

- `whatsnew()` → returns `[]`
- `list_messages()` → returns `[]`
- `read_message()` → raises `NotImplementedError` (only called with explicit ID, never in bulk)
- `read_thread()` → raises `NotImplementedError` (same)

This follows the "platform failures are isolated" design rule — a gcal source silently contributes nothing to messaging listings rather than crashing the entire multi-source fetch.

## Adapter

### File: `src/ts4k/adapters/gcal.py`

`GcalAdapterConfig` dataclass:
- `email: str` — Google account email
- `calendar_id: str` — Google Calendar ID (e.g. `"primary"`, `"family123@group.calendar.google.com"`)
- `calendar_name: str` — human-readable name for display
- `timezone: str` — IANA timezone (e.g. `"Europe/Amsterdam"`) from `calendarList.list` response
- `config_dir: Path | None` — config directory (default `None`, resolved to `~/.config/ts4k`)
- `level: str` — access level string (default `"readonly"`)

`GcalAdapter(BaseAdapter)`:
- `__init__(config, prefix)` — stores config, sets `_access_level` from `config.level`
- `connect()` — calls `build_calendar_service(email, config_dir, scopes)` via `asyncio.to_thread`
- `disconnect()` — calls `self._service.close()` (matches Gmail adapter pattern for cleaning up the `httplib2.Http` transport)
- `__aenter__` / `__aexit__` — context manager pattern matching Gmail adapter

### Calendar-Specific Methods

These are the primary interface for calendar commands.

```python
async def list_events(
    self,
    time_min: str,          # ISO 8601 datetime (timezone-aware)
    time_max: str,          # ISO 8601 datetime (timezone-aware)
    count: int = 250,
) -> list[dict]:
    """Fetch events in a time range. Returns normalized event dicts.

    Uses singleEvents=True to expand recurring events into instances,
    and orderBy=startTime (required when singleEvents=True).
    Excludes cancelled events by default.

    Follows nextPageToken to paginate through all results up to count.
    Each expanded instance includes recurringEventId for collapsing.
    """

async def read_event(self, event_id: str) -> dict:
    """Fetch full detail for a single event via events.get (not the list path)."""

async def list_calendars(self) -> list[dict]:
    """List all calendars for this account. Used by setup wizard.
    Follows nextPageToken for accounts with many calendars.
    Filters out freeBusyReader-only calendars.
    """

async def create_event(
    self,
    title: str,
    start: str,             # ISO 8601 datetime, or date for all-day (inclusive)
    end: str,               # ISO 8601 datetime, or date for all-day (inclusive last day)
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,  # email addresses
) -> dict:
    """Create an event. Level gating:
    - If attendees provided: self._check_send("create_event") — SEND level required
    - If no attendees:       self._check_draft("create_event") — DRAFT level required

    When attendees are provided (SEND level), uses sendUpdates='all' to notify them.
    When no attendees (DRAFT level), uses sendUpdates='none'.

    All-day event dates: the CLI accepts inclusive start and end dates
    (e.g., --start 2026-03-17 --end 2026-03-21 means Mon-Fri).
    The adapter adds +1 day to end before sending to Google API, which
    uses exclusive end dates. This prevents off-by-one errors.
    """

async def update_event(
    self,
    event_id: str,
    **fields,               # any subset of title, start, end, description, location
) -> dict:
    """Update an existing event. Requires MODIFY level."""

async def rsvp(
    self,
    event_id: str,
    status: str,            # "accepted", "declined", "tentative"
) -> dict:
    """RSVP to an event. Requires MODIFY level.

    Uses events.patch to update self-attendee status.
    Uses sendUpdates='all' which notifies ALL attendees (not just
    the organizer) — this is Google API behavior.
    """
```

### Google Calendar API Call Shape

The core listing call (paginated):

```python
events = []
page_token = None
while len(events) < count:
    result = service.events().list(
        calendarId=self._calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        maxResults=min(count - len(events), 250),  # API max is 2500
        singleEvents=True,       # expand recurring events to instances
        orderBy="startTime",     # required with singleEvents=True
        pageToken=page_token,
    ).execute()
    events.extend(result.get("items", []))
    page_token = result.get("nextPageToken")
    if not page_token:
        break
```

All Google API calls wrap in `asyncio.to_thread()`, matching the Gmail adapter pattern. Event IDs are prefixed: `f"{prefix}:{native_event_id}"`.

### All-Day Event Date Semantics

Google Calendar API uses **exclusive end dates** for all-day events: an event on March 17 has `start.date = "2026-03-17"` and `end.date = "2026-03-18"`. A Mon-Fri event (5 days) has `end.date` = Saturday.

ts4k normalizes this for user-facing interfaces:
- **CLI input**: users provide **inclusive** start and end dates (`--start 2026-03-17 --end 2026-03-21` = Mon through Fri). The adapter adds +1 day to end before the API call.
- **Display output**: shows inclusive date ranges (`Mar 17-21`, not `Mar 17-22`). The adapter subtracts 1 day from the API's end date for display.
- **Normalized event dict**: `all_day: True`, `start` and `end` contain the raw API dates (exclusive end). Display normalization happens in the formatter.

### Timezone Handling

- `cal today`, `cal tomorrow`, `cal week` compute time boundaries using the **calendar's configured timezone** (`timezone` field in `sources.json`, populated by `cal setup` from the `calendarList.list` response).
- Google Calendar API returns timed events with `dateTime` keys (ISO 8601 with offset) and all-day events with `date` keys (date-only, no timezone). The adapter normalizes both into the event dict, setting `all_day: True/False` accordingly.
- All-day events use the calendar's timezone for boundary computation. An all-day event on Mar 11 means midnight-to-midnight in the calendar's timezone.

### Default Filtering

- **Cancelled events** (`status: "cancelled"`): excluded from listings by default. Google API supports `showDeleted=False` (the default).
- **Declined events** (`your_status: "declined"`): included in listings but marked with status. Agents and users may want to know about declined events for context (e.g., "you declined this meeting with Sarah, but she emailed you about it"). The pipe format shows a `(declined)` indicator.

### Recurring Event Collapsing

With `singleEvents=True`, a weekly recurring event over 12 months expands to 52+ rows — massive token waste. The adapter returns all expanded instances, but the **formatter collapses recurring series** based on `recurringEventId` (provided by Google on every expanded instance).

**Collapsing rules by view:**

| View | Behavior |
|------|----------|
| `cal today` / `cal tomorrow` | Show every instance individually (need to know what's on today) |
| `cal week` | Show every instance, annotate recurring ones: `(weekly)`, `(daily)`, etc. |
| `cal next` / `cal range` (multi-week) | Collapse recurring series into one row with pattern + next occurrence |

**Collapsed row format:**
```
REF|SOURCE|TIME|DUR|TITLE|LOCATION|ATTENDEES
1|gc|Mon,Thu 19:00-20:00|1h|Team Sync|Zoom|5 people (weekly, 2x/wk)
2|gc|Mon 09:00-09:30|30m|Standup|Zoom|3 people (weekly)
3|gcf|Mar 19 all-day||School Holiday||
```

**How it works:**
1. Group expanded instances by `recurringEventId`
2. For groups with 2+ instances in multi-week views: collapse into one row
3. Derive the recurrence pattern from the parent event's `recurrence` field (simple RRULE-to-human: `FREQ=WEEKLY;BYDAY=MO,TH` → `weekly, Mon+Thu`)
4. Show the next upcoming occurrence as the time, with pattern in parentheses
5. Single-occurrence events and non-recurring events pass through unchanged

**Expanding collapsed events:** `ts4k cal event <ref>` on a collapsed recurring row shows the recurrence rule, next N instances (default 5), and full event detail.

**Detection:** Uses `recurringEventId` from the API — no RRULE parsing needed for grouping. RRULE parsing is only needed for the human-readable summary, and only for common patterns (daily, weekly, biweekly, monthly). Complex RRULEs fall back to showing the raw rule.

### Event Normalization

`list_events` returns compact header dicts. Each expanded recurring instance includes `recurring_event_id` for the formatter's collapsing logic:

```python
{
    "id": "gc:eventId123",
    "source": "gc",
    "title": "Standup",
    "start": "2026-03-11T09:00:00+01:00",
    "end": "2026-03-11T09:30:00+01:00",
    "all_day": False,
    "duration_minutes": 30,
    "location": "Zoom",
    "organizer": "sarah@work.com",
    "attendees_summary": "3 people",
    "status": "confirmed",
    "your_status": "accepted",
    "recurring_event_id": "gc:baseEventId456",  # None if not recurring
}
```

`read_event` returns full detail:

```python
{
    # ... all fields from above, plus:
    "description": "Review Q1 numbers and plan Q2 allocation.",
    "meeting_link": "https://meet.google.com/xyz",
    "attendees": [
        {"name": "Sarah Chen", "email": "sarah@work.com", "status": "accepted"},
        {"name": "Mike R", "email": "mike@work.com", "status": "tentative"},
    ],
    "recurrence": "RRULE:FREQ=WEEKLY;BYDAY=TU",
    "recurrence_summary": "weekly on Tuesdays",
    "created": "2026-01-15T10:00:00Z",
    "updated": "2026-03-10T14:30:00Z",
}
```

## Auth

### File: `src/ts4k/auth/google.py`

Add one function:

```python
def build_calendar_service(email: str, config_dir: Path | None = None, scopes: list[str] | None = None):
    """Build a Google Calendar API v3 service client."""
    creds = get_credentials(email, scopes or [], config_dir)
    return build("calendar", "v3", credentials=creds)
```

`get_credentials()` is reused unchanged. It already handles:
- Loading existing tokens from `~/.config/ts4k/google/<email>/token.json`
- Checking if stored scopes are a superset of needed scopes
- Triggering re-auth when scopes expand (e.g., adding calendar to an existing Gmail account)
- Token refresh

### Auth Command Integration

The existing `ts4k auth gmail` command (in `cli.py`) derives scopes only from Gmail sources for a given email. This must be extended to also include gcal sources for the same email, so that `ts4k auth <email>` requests the union of all needed scopes (Gmail + Calendar) in a single OAuth consent screen.

Alternatively, add `ts4k auth gcal` as a separate auth path. The simpler approach is to make the existing auth command provider-agnostic: scan all Google-authed sources (gmail + gcal) for the email and union their scopes.

## Access Levels

### File: `src/ts4k/core/levels.py`

**Scope map:** Add `_GCAL_SCOPES` mapping and a `"gcal"` branch in `scopes_for()`:

```python
_GCAL_SCOPES = {
    AccessLevel.READONLY: ["https://www.googleapis.com/auth/calendar.readonly"],
    AccessLevel.MODIFY:   ["https://www.googleapis.com/auth/calendar"],
    AccessLevel.DRAFT:    ["https://www.googleapis.com/auth/calendar"],
    AccessLevel.SEND:     ["https://www.googleapis.com/auth/calendar"],
}
```

Google Calendar API has only two scope tiers (`calendar.readonly` and `calendar`). The distinction between MODIFY, DRAFT, and SEND is enforced in code, not by OAuth scopes — same pattern as Gmail where `gmail.modify` covers modify/draft/send.

| Level | OAuth Scope | Adapter Behavior |
|-------|------------|-----------------|
| READONLY | `calendar.readonly` | `list_events`, `read_event` only |
| MODIFY | `calendar` | + `update_event`, `rsvp` |
| DRAFT | `calendar` | + `create_event` (no attendees) |
| SEND | `calendar` | + `create_event` with attendees (sends invite notifications) |

**SEND level for calendar:** The existing `check_level()` function unconditionally blocks SEND with `NotImplementedError("ts4k never sends messages")`. This guard must be made provider-aware:

```python
def check_level(current: AccessLevel, required: AccessLevel, operation: str,
                *, provider: str | None = None) -> None:
    """Raise PermissionError if current level is below required."""
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

Calendar invites are a different class than sending email — they're a normal collaborative workflow. The SEND level for `gcal` is explicitly permitted. The messaging SEND block remains unchanged.

**Level check helpers:** Following the existing adapter pattern (`_check_modify`, `_check_draft` in Gmail/O365 adapters), the gcal adapter adds:

```python
def _check_modify(self, operation: str) -> None:
    check_level(self._access_level, AccessLevel.MODIFY, operation, provider="gcal")

def _check_draft(self, operation: str) -> None:
    check_level(self._access_level, AccessLevel.DRAFT, operation, provider="gcal")

def _check_send(self, operation: str) -> None:
    check_level(self._access_level, AccessLevel.SEND, operation, provider="gcal")
```

## Output Format

### File: `src/ts4k/core/format.py`

Add `format_events()` for listings and `format_event_detail()` for single events.

### Listing Format

Pipe-delimited with adaptive time column based on the span of results:

**Today / single day:**
```
REF|SOURCE|TIME|DUR|TITLE|LOCATION|ATTENDEES
1|gc|09:00-09:30|30m|Standup|Zoom|3 people
2|gc|11:00-12:00|1h|Q1 Budget Review|Room 4A|Sarah, Mike +2
3|gcw|14:00-15:00|1h|1:1 with Sarah|Teams|Sarah Chen
4|gcf|all-day||School Holiday||
```

**Multi-day (within same week):**
```
REF|SOURCE|TIME|DUR|TITLE|LOCATION|ATTENDEES
1|gc|Mon 09:00-09:30|30m|Standup|Zoom|3 people
2|gcf|Wed all-day||School Holiday||
3|gc|Fri 14:00-16:00|2h|Sprint Review|Room 2B|8 people
```

**Multi-week:**
```
REF|SOURCE|TIME|DUR|TITLE|LOCATION|ATTENDEES
1|gc|Mar 17-21|5d|Vacation||
2|gcw|Mar 24 09:00-10:00|1h|Planning|Zoom|5 people
```

The formatter auto-selects the time format based on the date range of the result set:
- All events on same day → time only (`HH:MM-HH:MM`)
- Events span multiple days within ~7 days → day + time (`Mon HH:MM`)
- Events span more than 7 days → date + time (`Mar 17 HH:MM`) or date range for multi-day events (`Mar 17-21`)

All-day events show `all-day` with no duration column.

Declined events include a `(declined)` marker after the title.

### Event Detail Format

Mini XML matching the existing message detail pattern:

```xml
<ev ref="1" id="gc:abc123">
<title>Q1 Budget Review</title>
<when>Tue Mar 11, 11:00-12:00 (1h)</when>
<where>Room 4A</where>
<organizer>Sarah Chen (sarah@work.com)</organizer>
<your-status>accepted</your-status>
<attendees>
  Sarah Chen (accepted)
  Mike R (tentative)
  You (accepted)
  +2 others
</attendees>
<link>https://meet.google.com/xyz</link>
<recurrence>weekly on Tuesdays</recurrence>
<description>Review Q1 numbers and plan Q2 allocation.</description>
</ev>
```

JSON and XML formats are also supported via the standard `--format` flag.

## Commands

### File: `src/ts4k/commands.py`

**Imports:** Add `GcalAdapter` and `GcalAdapterConfig` to the top-level imports alongside the other adapters, and add `GcalAdapter` to the `_make_adapter()` return type union.

**New functions** returning `CommandResult` (or strings for simple output):

```python
async def cal_today(source, fmt, ref_table) -> CommandResult
async def cal_tomorrow(source, fmt, ref_table) -> CommandResult
async def cal_week(source, fmt, ref_table) -> CommandResult
async def cal_next(source, count, fmt, ref_table) -> CommandResult
async def cal_range(source, from_date, to_date, fmt, ref_table) -> CommandResult
async def cal_event(ref_or_id, source, fmt, ref_table) -> str
async def cal_list_calendars(email, config_dir) -> list[dict]  # non-interactive, for setup wizard
async def cal_create(source, title, start, end, description, location, attendees, ref_table) -> str
async def cal_update(ref_or_id, source, ref_table, **fields) -> str
async def cal_rsvp(ref_or_id, source, status, ref_table) -> str
```

These call `GcalAdapter` directly (not through `_fetch_messages`). The `source` parameter filters to specific calendar sources; if omitted, queries all `gcal` provider sources and merges results sorted by start time.

Ref table integration: listing commands populate the ref table so `ts4k cal event 3` resolves to the third event from the last listing.

### Adapter Factory

Add to `_make_adapter()`:
```python
if provider == "gcal":
    config = GcalAdapterConfig(email=cfg["email"], calendar_id=cfg["calendar_id"], ...)
    return GcalAdapter(config, prefix=prefix)
```

### Provider Aliases

Add to `provider_map` in `_resolve_prefixes()`:
```python
provider_map = {
    ...,
    "google-calendar": "gcal", "calendar": "gcal", "cal": "gcal",
}
```

## CLI

### File: `src/ts4k/cli.py`

Add `cal` subparser with subcommands:

| Subcommand | Arguments | Handler |
|-----------|-----------|---------|
| `cal` (no sub) | `--source`, `--format` | → `cal_today` |
| `cal today` | `--source`, `--format` | → `cal_today` |
| `cal tomorrow` | `--source`, `--format` | → `cal_tomorrow` |
| `cal week` | `--source`, `--format` | → `cal_week` |
| `cal next` | `-n COUNT`, `--source`, `--format` | → `cal_next` |
| `cal range` | `--from DATE`, `--to DATE`, `--source`, `--format` | → `cal_range` |
| `cal event REF` | `--source`, `--format` | → `cal_event` |
| `cal setup` | (interactive) | → `cal_setup` (interactive logic in cli.py, calls `cal_list_calendars` + `sources.add`) |
| `cal create` | `--title`, `--start`, `--end`, `--description`, `--location`, `--attendees`, `--source` | → `cal_create` |
| `cal update REF` | `--title`, `--start`, `--end`, `--description`, `--location`, `--source` | → `cal_update` |
| `cal rsvp REF` | `--status accepted/declined/tentative`, `--source` | → `cal_rsvp` |

No single-letter alias — `c` is taken by `contacts`. Use `cal` only.

## MCP Server

### File: `src/ts4k/server.py`

Add read tool:

```python
@mcp.tool()
async def cal(
    view: str = "today",           # today|tomorrow|week|next|range|event
    ref: str | None = None,        # for event detail
    source: str | None = None,     # filter to specific calendar
    count: int = 10,               # for 'next' view
    from_date: str | None = None,  # for 'range' view
    to_date: str | None = None,    # for 'range' view
    format: str = "pipe",
) -> str:
    """Calendar: view events across Google Calendar sources."""
```

Add write tools:

```python
@mcp.tool()
async def cal_create(
    source: str,                   # which calendar to create on
    title: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    attendees: str | None = None,  # comma-separated emails
) -> str:
    """Create a calendar event. Requires draft level (no attendees) or send level (with attendees)."""

@mcp.tool()
async def cal_manage(
    action: str,                   # update|rsvp
    ref: str,
    source: str | None = None,
    status: str | None = None,     # for rsvp: accepted|declined|tentative
    title: str | None = None,      # for update
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> str:
    """Modify a calendar event or RSVP. Requires modify level."""
```

## Dependencies

No new dependencies. `google-api-python-client` and `google-auth-oauthlib` are already in `pyproject.toml` for the Gmail adapter. The Calendar API v3 uses the same client library.

## Out of Scope

- O365 Calendar (Phase 6b — separate adapter, same pattern)
- Attendee context enrichment (Phase 6c — depends on this adapter existing)
- Free/busy or conflict detection
- Calendar sharing or delegation

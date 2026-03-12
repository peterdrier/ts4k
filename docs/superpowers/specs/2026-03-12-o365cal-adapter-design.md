# O365 Calendar Adapter Design (Phase 6b)

## Overview

Add O365 calendar support to ts4k at the same level of functionality as the existing Google Calendar adapter. Uses Microsoft Graph API, reuses existing O365 auth infrastructure, and produces the same normalized event format so the format layer, CLI, and MCP tools require zero changes.

## Approach

Separate adapter file (`adapters/o365cal.py`) mirroring the `gcal.py` / `gmail.py` split. Provider name `o365cal`. Source prefix is user-chosen during setup (e.g., `oc`).

## 1. Adapter (`adapters/o365cal.py`)

New `O365CalAdapter` class with config dataclass:

- `email` — O365 account email (links to MSAL token cache)
- `calendar_id` — Graph calendar ID or `"default"` for primary
- `calendar_name` — human display name
- `timezone` — IANA timezone string (from `GET /me/calendars`)
- `config_dir` — override for `~/.config/ts4k`
- `level` — `readonly` / `modify` / `draft` / `send`

### Graph API Endpoints

| Operation | Method | Endpoint |
|-----------|--------|----------|
| List events | GET | `/me/calendarView?startDateTime=X&endDateTime=Y` |
| Read event | GET | `/me/events/{id}` |
| List calendars | GET | `/me/calendars` |
| Create event | POST | `/me/calendars/{id}/events` |
| Update event | PATCH | `/me/events/{id}` |
| RSVP accept | POST | `/me/events/{id}/accept` |
| RSVP decline | POST | `/me/events/{id}/decline` |
| RSVP tentative | POST | `/me/events/{id}/tentativelyAccept` |

### Normalization

Produces the same dict shape as `GcalAdapter`:

- `id`: `"{prefix}:{graph_event_id}"`
- `source`, `title`, `start`, `end`, `all_day`, `duration_minutes`
- `location`, `organizer`, `attendees_summary`
- `status`, `your_status`, `recurring_event_id`
- Detail-only fields: `attendees`, `description`, `meeting_link`, `recurrence`, `recurrence_summary`, `created`, `updated`

### Recurring Events

`/me/calendarView` automatically expands recurring events into instances (analogous to Google's `singleEvents=True`). Each instance has a `seriesMasterId` field mapped to `recurring_event_id`. The format layer's existing `_collapse_recurring()` works unchanged.

### All-Day Events

Graph API uses `isAllDay: true` with `dateTime` fields in date-only format. Adapter detects this and sets `all_day: True`. End date handling: Graph uses exclusive end dates (same as Google), so the same display-side -1 day adjustment applies.

### Auth

Reuses `build_graph_client()` from `auth/microsoft.py`. Gets an `httpx.AsyncClient` with Bearer token at `connect()` time via `asyncio.to_thread()`. Uses the same `_get()`, `_post()`, `_patch()` helper pattern as `O365Adapter`.

### RSVP

Graph uses separate endpoints per response (`/accept`, `/decline`, `/tentativelyAccept`) rather than patching attendee status. The adapter maps the `status` parameter (`accepted`/`declined`/`tentative`) to the correct endpoint.

## 2. Scopes & Levels (`core/levels.py`)

Add `_O365_CAL_SCOPES`:

| Level | Scope |
|-------|-------|
| READONLY | `Calendars.Read` |
| DRAFT | `Calendars.ReadWrite` |
| MODIFY | `Calendars.ReadWrite` |
| SEND | `Calendars.ReadWrite` |

Extend `check_level()` SEND exception for calendar invites to include `o365cal` provider.

## 3. Commands & Wiring (`commands.py`)

- **`_make_adapter()`**: Add `o365cal` branch constructing `O365CalAdapter` from source config.
- **`_cal_fetch_events()`**: Expand provider filter from `gcal` only to include `o365cal`. Both providers iterated, events merged and sorted by `start`.
- **`_get_cal_timezone()`**: Expand to include `o365cal` sources.
- **Ref resolution**: Already works by parsing source prefix from event ID. No changes needed.
- **Format layer**: Zero changes. Operates on normalized dicts.
- **CLI handlers**: Zero changes. Delegate to command functions.
- **MCP tools**: Zero changes. Delegate to command functions.

## 4. Setup Wizard & Auth

### Auth

`ts4k auth o365` currently requests only mail scopes. Add calendar scopes by default with `--no-calendar` opt-out (matching the Gmail auth pattern). Scopes added to the MSAL device code flow request.

### Setup

Extend `ts4k cal setup` to:

1. Scan `sources.json` for O365 mail sources (in addition to Gmail sources)
2. For each O365 account, call `GET /me/calendars` via adapter's `list_calendars()`
3. Display calendars with suggested prefixes
4. Let user pick prefix and access level
5. Save to `sources.json` with `provider: "o365cal"`

If no O365 mail source exists, prompt user to run `ts4k auth o365` first.

### Source Config

```json
{
  "oc": {
    "provider": "o365cal",
    "email": "user@company.com",
    "calendar_id": "AAMkAG...",
    "calendar_name": "Work Calendar",
    "timezone": "Europe/Amsterdam",
    "level": "readonly"
  }
}
```

## 5. Tests

| File | Covers | Mirrors |
|------|--------|---------|
| `test_o365cal_adapter.py` | list_events, read_event, list_calendars, pagination, recurring, declined | `test_gcal_adapter.py` |
| `test_o365cal_write.py` | create_event, update_event, rsvp, level gating | `test_gcal_write.py` |
| `test_cal_commands.py` (extend) | o365cal source cases, multi-provider merge/sort, cross-provider ref resolution | existing file |

Mock `httpx.AsyncClient` responses matching Graph API JSON format (same mock pattern as O365 email adapter tests).

## Non-Goals

- No calendar-specific caching or watermarks (events fetched live, same as gcal)
- No shared/delegated calendar support beyond what `list_calendars` exposes
- No free/busy lookup API

# O365 Calendar Adapter Design (Phase 6b)

## Overview

Add O365 calendar support to ts4k at the same level of functionality as the existing Google Calendar adapter. Uses Microsoft Graph API, reuses existing O365 auth infrastructure, and produces the same normalized event format so the format layer, CLI, and MCP tools require zero changes.

## Approach

Separate adapter file (`adapters/o365cal.py`) mirroring the `gcal.py` / `gmail.py` split. Provider name `o365cal`. Source prefix is user-chosen during setup (e.g., `oc`).

## 1. Adapter (`adapters/o365cal.py`)

New `O365CalAdapter` class with config dataclass:

- `email` — O365 account email (links to MSAL token cache)
- `client_id` — Azure app registration client ID (same as O365 mail source)
- `tenant_id` — Azure tenant ID (same as O365 mail source)
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

### Recurrence Summary

Graph API returns a structured `recurrence` object with `pattern` (type, interval, daysOfWeek) and `range`, not RRULE strings. The adapter implements `graph_recurrence_to_human(recurrence_dict)` to convert this to a human string (e.g., `{"pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["monday", "thursday"]}}` → `"weekly on Mon+Thu"`). This is the Graph equivalent of `rrule_to_human()` in `gcal.py`. The raw recurrence dict is stored in the `recurrence` field; the human string goes in `recurrence_summary`.

### All-Day Events

Graph API uses `isAllDay: true` with `dateTime` fields in date-only format. Adapter detects this and sets `all_day: True`. End date handling: Graph uses exclusive end dates (same as Google), so the same display-side -1 day adjustment applies.

### Auth

Reuses `build_graph_client()` from `auth/microsoft.py`, passing `client_id` and `tenant_id` from the config (same values as the linked O365 mail source). Gets an `httpx.AsyncClient` with Bearer token at `connect()` time via `asyncio.to_thread()`. Requests calendar-specific scopes via `scopes_for("o365cal", level)`. Uses the same `_get()`, `_post()`, `_patch()` helper pattern as `O365Adapter`.

Level-checking helpers follow the gcal pattern: `_check_modify()`, `_check_draft()`, `_check_send()` each call `check_level(required, self._access_level, provider="o365cal")`. The `provider="o365cal"` argument is required so the SEND guard in `check_level()` correctly allows calendar invites.

### RSVP

Graph uses separate endpoints per response (`/accept`, `/decline`, `/tentativelyAccept`) rather than patching attendee status. The adapter maps the `status` parameter (`accepted`/`declined`/`tentative`) to the correct endpoint. These endpoints return HTTP 202 with an empty body (no JSON), so the `rsvp()` method must not call `resp.json()` on the response. Instead, after a successful RSVP POST, re-fetch the event via `read_event()` to return the updated normalized event.

## 2. Scopes & Levels (`core/levels.py`)

Add `_O365_CAL_SCOPES`:

| Level | Scope |
|-------|-------|
| READONLY | `Calendars.Read` |
| DRAFT | `Calendars.ReadWrite` |
| MODIFY | `Calendars.ReadWrite` |
| SEND | `Calendars.ReadWrite` |

Extend `check_level()` SEND exception for calendar invites: change `provider != "gcal"` guard to `provider not in ("gcal", "o365cal")`.

Add `"o365cal"` branch to `scopes_for()`: `if provider == "o365cal": return list(_O365_CAL_SCOPES.get(level, []))`.

## 3. Commands & Wiring (`commands.py`)

- **`_make_adapter()`**: Add `o365cal` branch constructing `O365CalAdapter` from source config (reads `client_id`, `tenant_id`, `email`, `calendar_id`, `calendar_name`, `timezone`, `level`, `config_dir`).
- **`_cal_fetch_events()`**: Expand provider filter from `gcal` only to include `o365cal`. The first filter clause (`provider != "gcal"`) becomes `provider not in ("gcal", "o365cal")`. The second clause (`source and pfx != source and cfg.get("provider") != source`) already works for `--source o365cal` by provider name — no change needed there. Both providers iterated, events merged and sorted by `start`.
- **`cal_event`, `cal_create`, `cal_update`, `cal_rsvp`**: These functions contain hardcoded `provider != "gcal"` guards that reject non-gcal sources. Expand all four to accept `o365cal` as well (e.g., `provider not in ("gcal", "o365cal")`).
- **`_get_cal_timezone()`**: Expand to include `o365cal` sources. When `source=None` and both gcal and o365cal sources exist, use the first source found (same as current behavior — the `--source` flag is the intended disambiguation mechanism).
- **`cal_list_o365_calendars(email, client_id, tenant_id, config_dir)`**: New command function (parallel to `cal_list_calendars` for gcal) that creates a temporary `O365CalAdapter`, calls `list_calendars()`, and returns the result. Used by the setup wizard.
- **Ref resolution**: Already works by parsing source prefix from event ID. No changes needed.
- **Format layer**: Zero changes. Operates on normalized dicts.
- **`_resolve_prefixes()` provider_map**: Add aliases for `o365cal`: `"o365-calendar": "o365cal"`, `"outlook-calendar": "o365cal"`. This enables `--source o365-calendar` as a shorthand (matching the `"google-calendar": "gcal"` pattern).
- **CLI handlers**: Zero changes. Delegate to command functions.
- **MCP tools**: Zero changes. Delegate to command functions.

## 4. Setup Wizard & Auth

### Auth

`ts4k auth o365` currently requests only mail scopes via `get_credentials()` with default `GRAPH_MAIL_READ_SCOPES`. Add `Calendars.Read` by default (matching the Gmail auth pattern where `calendar.readonly` is included by default). Add `--no-calendar` argparse flag to `au_o365` subparser to opt out. When existing `o365cal` sources exist with levels above READONLY, collect the higher scopes (`Calendars.ReadWrite`) from their configs — same logic as the Gmail auth flow does for gcal sources (cli.py lines 839-847).

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
    "client_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "tenant_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
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

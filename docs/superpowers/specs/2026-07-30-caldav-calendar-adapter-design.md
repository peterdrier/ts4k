# CalDAV Calendar Adapter (Apple/iCloud preset) — Design

**Date:** 2026-07-30
**Status:** Approved

## Goal

Add Apple Calendar (iCloud) support to ts4k as a generic CalDAV adapter, so
`cal`-family commands work against iCloud the same way they do against Google
Calendar and O365 Calendar. Generic CalDAV means the same adapter later serves
Fastmail, Nextcloud, or any RFC 4791 server with zero new code.

## Decisions Made

- **Generic CalDAV provider** (`caldav`), with an Apple preset — aliases
  `apple`, `icloud`, `apple-calendar` pre-fill `server_url` with
  `https://caldav.icloud.com`.
- **Full surface at launch**: list/read/create/update events, list calendars,
  and RSVP (with graceful degradation — see RSVP section).
- **Credentials** stored as plaintext JSON in the config dir, consistent with
  existing Google/O365 token storage.
- **Implementation via the `caldav` PyPI library** (not hand-rolled httpx) —
  it owns principal discovery, REPORT queries, and per-server quirks. This is
  the "ts4k does not reimplement platform protocols" rule applied to CalDAV.

## Provider & Registration

- Provider key: `caldav`. Default prefix: `cc:` (alongside `gc:` Google cal,
  `oc:` O365 cal).
- Registered in the `commands.py` adapter factory next to `gcal`/`o365cal`.
- Alias map entries: `"apple" | "icloud" | "apple-calendar" → "caldav"`.
- Every calendar-command gate currently written as
  `provider in ("gcal", "o365cal")` extends to include `"caldav"`.
- Provider label for status output: `"CalDAV"`.

## Adapter

New file `src/ts4k/adapters/caldav_cal.py` (named to avoid import collision
with the `caldav` package).

### Config

```python
@dataclass
class CaldavAdapterConfig:
    email: str            # account identity (Apple ID)
    server_url: str       # e.g. https://caldav.icloud.com
    calendar_id: str      # CalDAV calendar URL/path
    calendar_name: str = ""
    timezone: str = "UTC"
    config_dir: Path | None = None
    level: str = "readonly"
```

### Class

`CaldavAdapter(BaseAdapter)`, mirroring `GcalAdapter`'s surface:

- `connect()` — build `caldav.DAVClient` from stored credentials, resolve the
  principal once. `disconnect()` closes it. Async context-manager support like
  the other adapters.
- `list_events(...)`, `read_event(...)`, `list_calendars()`,
  `create_event(...)`, `update_event(...)`, `rsvp(...)`.
- Message-side methods stubbed exactly like `gcal.py`: `whatsnew`/
  `list_messages` return `[]`; `read_message`/`read_thread` raise
  `NotImplementedError`.
- The `caldav` library is synchronous — every call to it is wrapped in
  `asyncio.to_thread` (same wrap-a-sync-client pattern as the Google adapter).

### Normalization

Events normalize to the **same dict shape** `GcalAdapter._normalize_event`
produces — identical keys, so `core/format.py` needs no changes. RRULEs render
via the existing module-level `rrule_to_human` imported from `gcal.py`.
VEVENT cases to handle: recurring (RRULE), all-day (DATE values), and floating
times (no TZID → interpret in the source's configured timezone).

**Recurring events are expanded into instances** within the queried time
window, matching gcal's `singleEvents=True` behavior. Use the `caldav`
library's expand support (server-side `CALDAV:expand` where the server honors
it, client-side expansion as fallback). Each instance carries
`recurring_event_id` pointing at the master's UID, same as gcal's
normalized shape. Results sorted by start time.

## Auth & Levels

- Credentials file: `~/.config/ts4k/caldav/<email>/credentials.json`,
  written with `0600` permissions:

  ```json
  {"username": "...", "app_password": "...", "server_url": "..."}
  ```

- No OAuth. `scopes_for("caldav", level)` returns `[]`; access levels act as
  purely local gates through the existing `check_level` machinery
  (readonly → list/read only; modify → create/update/RSVP).
- Setup flow (source-add command): prompt for the app-specific password
  **interactively** — never as a CLI argument (shell history). Validate by
  connecting and listing calendars; on success write credentials + source
  entry. Point the user at appleid.apple.com to mint the app-specific
  password (requires 2FA on the Apple ID).

## RSVP

Best-effort, honest about outcomes:

1. Locate the `ATTENDEE` property matching the account email; set `PARTSTAT`
   (ACCEPTED/DECLINED/TENTATIVE) and save the event back.
2. Where the server advertises CalDAV scheduling support, also send the iTIP
   REPLY so the organizer is notified.
3. iCloud frequently rejects scheduling operations for externally-organized
   invites. On rejection, return an actionable error — e.g. *"RSVP not
   accepted by server — respond in the Calendar app"* — never a stack trace.
4. Partial success is reported as such (PARTSTAT updated locally, organizer
   not notified).

## Errors

- 401 / auth failure → message directing the user to generate a new
  app-specific password at appleid.apple.com (they expire when the Apple ID
  password changes).
- Server down / timeout → per the platform-isolation rule, the failure is
  contained; other adapters still return results.

## Search

- Time-range filtering happens server-side (CalDAV `calendar-query`).
- Free-text search is client-side over the fetched window — iCloud's
  `text-match` support is unreliable. Documented limitation.

## Testing

- Unit tests with a mocked `caldav` client, following the existing
  mock-adapter pattern (`tmp_path` + `monkeypatch` for state isolation):
  - VEVENT normalization: recurring, all-day, floating-time, recurrence sets
  - Level gating (readonly blocks create/update/rsvp)
  - RSVP fallback paths (server accepts / rejects / partial)
  - Credential loading and 0600 permission enforcement
  - Adapter factory: aliases resolve, `caldav` provider builds
- Manual smoke-test checklist against the real iCloud account (in the
  implementation plan): list calendars, list/read events, create, update,
  RSVP attempt on a real invite.

## Dependencies

- Add `caldav` to `pyproject.toml` dependencies (pulls `icalendar`, `lxml`,
  `vobject`).

## Out of Scope

- Push/webhook notifications (CalDAV has none ts4k can use; polling is fine).
- Server-side free-text search.
- Contact/message features for this provider — calendar only.
- Any Google/O365 adapter changes beyond the shared gate lists.

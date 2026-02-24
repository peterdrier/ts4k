# Phase 6: Calendar — Implementation Plan

**Status:** Not started. Depends on Phase 5 completion.

## Context

ts4k Phase 1-4 built the messaging pipeline: adapters, normalize, cache, overview. Phase 5 adds send/draft + release. Phase 6 extends ts4k beyond messaging into calendar — not for token savings (calendar data is already compact), but for **cohesive person context**.

The killer use case: "You have a meeting with Sarah in 40 minutes. Here's what you've been discussing with her this week across email and WhatsApp." That cross-reference between calendar attendees and message history is something no single platform provides.

## Value Proposition

| Aspect | Messages (Phase 1-4) | Calendar (Phase 6) |
|--------|---------------------|---------------------|
| Token savings | 60-95% (main value) | Modest — events are already small |
| Unified view | High — 3 platforms, different formats | High — Google Cal + O365, different schemas |
| Cross-reference | Within messages only | Attendees ↔ message history (new capability) |
| Agent efficiency | Fewer tool calls, compact output | "What's my day" in one call instead of 2+ |

## Design Principles

1. **Read-only.** No create/accept/decline initially. Same pattern as messaging Phase 1-4.
2. **Reuse existing adapters where possible.** O365 calendar is on the same Softeria MCP server that already handles mail. Google Calendar has a working MCP (`@cocal/google-calendar-mcp`).
3. **Calendar events flow through the same normalize → format pipeline.** Consistent output for the agent.
4. **Attendee enrichment is the headline feature.** Cross-reference attendees against the contacts map to surface message history context.
5. **Source prefix convention:** `gc` for Google Calendar, reuse existing `o*` prefix for O365 calendar (same source, different data type).

## Command Surface

```
ts4k cal today                          # today's events, all calendar sources
ts4k cal today --source gc              # Google Calendar only
ts4k cal week                           # this week's events
ts4k cal next                           # next upcoming event (with attendee context)
ts4k cal range 2026-03-01..2026-03-07   # specific date range
ts4k cal event <event_id>               # single event detail + attendee enrichment
```

### Output Format (pipe)

Listing:
```
gc:ev123|2026-02-25 09:00|2026-02-25 10:00|Sprint Planning|Room 3|alice,bob,charlie|accepted
gc:ev456|2026-02-25 14:00|2026-02-25 15:00|1:1 with Sarah|Zoom|sarah|accepted
```

Single event with attendee enrichment:
```
<event id="gc:ev456">
<title>1:1 with Sarah</title>
<when>2026-02-25 14:00–15:00</when>
<where>Zoom</where>
<attendees>
  sarah (you have 3 unread messages, last thread: "Q1 budget review")
  peter (organizer)
</attendees>
<notes>Discuss Q1 results</notes>
</event>
```

## Adapters

### Google Calendar Adapter

Wraps `@cocal/google-calendar-mcp` (Node.js, already installed on Peter's machine).

Key upstream tools to wrap:
- `list_events(timeMin, timeMax)` → listing
- `get_event(eventId)` → single event detail

Adapter config in `sources.json`:
```json
{
  "gc": {
    "provider": "google-calendar",
    "server_command": ["npx", "@cocal/google-calendar-mcp"],
    "calendar_id": "primary"
  }
}
```

### O365 Calendar Adapter

The Softeria `@softeria/ms-365-mcp-server` already supports calendar operations alongside mail. The existing O365 adapter connects to it — calendar support means adding calendar-specific tool calls to the same adapter (or a thin calendar sub-adapter that shares the connection).

Key upstream tools:
- `list_calendar_events(startDateTime, endDateTime)`
- `get_calendar_event(eventId)`

No new source entry needed — calendar events use the existing `o*` prefix with an `oc:` or similar namespace for event IDs.

## Normalized Event Schema

```python
{
    "id": "gc:ev123",
    "source": "gc",
    "title": "Sprint Planning",
    "start": "2026-02-25T09:00:00Z",
    "end": "2026-02-25T10:00:00Z",
    "location": "Room 3",
    "status": "accepted",           # accepted | tentative | declined | needsAction
    "organizer": "alice@example.com",
    "attendees": [
        {"email": "alice@example.com", "status": "accepted"},
        {"email": "bob@example.com", "status": "tentative"},
    ],
    "description": "...",           # normalized (HTML stripped)
    "recurring": False,
    "all_day": False,
}
```

## Attendee Context Enrichment

The headline feature. When rendering a single event (`ts4k cal event <id>` or `ts4k cal next`), cross-reference each attendee against:

1. **Contacts map** — resolve email → alias
2. **Message cache** — count recent messages, surface last thread subject
3. **Preload data** — if available, deeper history stats

This turns a bare calendar event into a meeting prep briefing. The agent doesn't have to make separate calls — ts4k does the join.

Implementation: a `_enrich_attendees()` helper in `commands.py` that takes an attendee list and returns enriched dicts with message context. Uses existing `contacts.find()` and `cache.list_headers()`.

## Files

| File | Action |
|------|--------|
| `src/ts4k/adapters/gcal.py` | **Create** — Google Calendar adapter |
| `src/ts4k/adapters/o365.py` | **Modify** — add calendar tool calls |
| `src/ts4k/commands.py` | **Modify** — `cal_today()`, `cal_week()`, `cal_event()`, `_enrich_attendees()` |
| `src/ts4k/core/format.py` | **Modify** — `format_event()`, `format_event_listing()` |
| `src/ts4k/cli.py` | **Modify** — `ts4k cal` subcommand |
| `src/ts4k/server.py` | **Modify** — `ts4k_cal` tool |
| `tests/test_gcal.py` | **Create** |
| `tests/test_cal_commands.py` | **Create** |

## Sub-phases

### 6a: Google Calendar Adapter + Basic Listing

- `gcal.py` adapter wrapping `@cocal/google-calendar-mcp`
- Normalized event schema
- `ts4k cal today`, `ts4k cal week`, `ts4k cal range`
- Pipe + JSON + XML output for event listings
- CLI + MCP tool

### 6b: O365 Calendar + Unified View

- Extend O365 adapter with calendar tool calls
- `ts4k cal today` merges Google + O365 calendars
- Conflict detection (overlapping events across sources)

### 6c: Attendee Context Enrichment

- `_enrich_attendees()` — contact resolution + message history lookup
- `ts4k cal event <id>` — full event detail with attendee context
- `ts4k cal next` — next event with attendee prep briefing
- Overview integration: `ts4k o --contact alice` could show calendar history too

## Verification

1. `ts4k cal today` — returns today's events across configured calendars
2. `ts4k cal event gc:ev123` — shows event with enriched attendee context
3. `ts4k cal next` — upcoming event with "you have N messages from attendee X"
4. `ts4k cal week --source gc` — Google Calendar only, pipe format
5. MCP: `ts4k_cal` tool works via server
6. All existing tests still pass

## Open Questions

1. **Event caching** — cache calendar events the same way we cache messages? Events change (rescheduled, cancelled) more than messages. Probably short TTL or no cache initially.
2. **Recurring events** — expand occurrences or show the series? Probably expand for the queried range, matching what the upstream MCPs return.
3. **Free/busy only?** — some calendars may only expose free/busy, not event details. Handle gracefully.
4. **Calendar write operations** — create event, RSVP. Deferred to a future phase (same as messaging send was deferred to Phase 5).

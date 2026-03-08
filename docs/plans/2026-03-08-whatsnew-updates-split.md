# Design: Split whatsnew from updates

## Problem

`updates`/`whatsnew`/`wn` are aliases for the same command that mixes two concerns: time-based message fetching and stateful watermark tracking. This prevents independent watermark contexts (e.g. "life admin" vs "personal") and makes the command harder to reason about.

## Design

### Shared fetch layer

```python
async def _fetch_messages(
    since: dict[str, str],   # prefix -> UTC ISO timestamp
    count: int = 20,
    source: str | None = None,
    fmt: str = "pipe",
    filter: bool = False,
    ref_table: RefTable | None = None,
) -> CommandResult:
```

- `since` maps source prefix to resolved UTC timestamp — no relative times, no watermark lookups
- Parallel fetch per source via `_fetch_one(prefix, timestamp)`
- Collate, sort by date descending, truncate to `count`, format
- Pure — no side effects (no watermark reads or writes)
- Both `updates` and `whatsnew` resolve timestamps to UTC before calling this

### `updates` command (stateless)

- CLI: `ts4k updates --since 2d --source o -n 20`
- Alias: `u`
- `--since` defaults to `1d`, accepts relative (`2d`, `6h`), ISO, or `all` (no time bound)
- Relative/absolute `--since` resolved to UTC timestamp before reaching shared layer
- No watermarks, no state side effects beyond stats recording

### `whatsnew` command (stateful)

- CLI: `ts4k whatsnew life`, `ts4k whatsnew life --source o -n 20`
- Alias: `wn`
- Key is required positional arg
- Loads per-source timestamps from key's watermark file
- First run per key defaults to 7 days back
- Calls shared fetch layer with resolved timestamps
- After fetch, advances per-source watermarks to the newest returned message per source
- Supports `--source` for consistency but primary use is all sources in parallel

### Watermark storage

- Directory: `~/.config/ts4k/watermarks/`
- One file per key: `life.json`, `peter.json`
- Contents: `{"g": "2026-03-08T12:00:14Z", "o": "2026-03-08T01:19:33Z"}`
- File-per-key avoids contention between concurrent whatsnew calls with different keys
- Old `watermarks.json` left on disk, no longer read

### Truncation and "more available"

When results across all sources exceed `count`, the output is truncated to `count` most recent messages. Watermarks advance per source only to the newest message that was actually returned.

Example: 100 total messages across 3 sources, count=20. First call returns 20 newest overall (maybe 10 Gmail, 5 WhatsApp, 5 O365). Each source's watermark advances to its newest returned message. Next call picks up from those timestamps, eventually draining all messages.

`CommandResult` gains:
- `has_more: bool`
- `remaining: int` (estimate: total fetched minus truncated count)

The agent can react by requesting larger chunks (`-n 50`) if it has context budget.

### Status display

Status command shows whatsnew keys summary:

```
Whatsnew keys:
  life: 3 sources, last run 2026-03-08T12:00:14Z
  peter: 2 sources, last run 2026-03-07T09:00:00Z
```

### Future extension

This design naturally extends to contact-scoped history retrieval — a key per contact with watermarks spanning all sources, fetching full cross-platform history for a person. Not in scope now.

## Data flow

```
whatsnew <key>  /  updates --since 2d
        |                    |
   resolve per-source    resolve single
   timestamps from       timestamp from
   key watermark file    relative/absolute
        |                    |
        +---- both build ----+
              dict[prefix, utc_ts]
                    |
        _fetch_messages(since={...})
           |-- _fetch_one("g", ts) --+
           |-- _fetch_one("o", ts) --| parallel
           +-- _fetch_one("w", ts) --+
                    |
              collate, sort, truncate, format
                    |
              CommandResult (has_more, remaining)
                    |
        whatsnew: save new watermarks
        updates: done
```

## Migration

- `updates` doesn't touch watermarks — no migration needed
- `whatsnew` starts fresh per key — no migration needed
- Old `watermarks.json` stops being read, left on disk
- Status switches from showing per-source watermarks to per-key summary

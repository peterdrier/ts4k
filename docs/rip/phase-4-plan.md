# Phase 4: Overview + Bulk — Implementation Plan

**Status:** Saved for later. Paused to handle PII emergency.

## Context

Phase 3 is complete (Gmail, WhatsApp, O365 adapters + MCP server mode). Phase 4 adds bulk historical capability — the ability to aggregate, preload, and efficiently query large message histories. The RIP success criterion: "10 years for < $100 LLM cost."

Four sub-features with clear dependencies (least → most dependent):

```
Cache ──────────┐
                ├──→ Resumable Batch ──→ Background Tasks
Pagination ─────┘         │
                          ▼
                      Overview
```

We'll implement in 4 sub-phases: 4a → 4b → 4c → 4d.

---

## Phase 4a: Message Cache

**Goal:** Local message store so repeated reads are free and aggregation is possible.

### Design

Cache is **explicit and user-initiated** (RIP anti-goal: "not an automatic crawl"). Messages enter cache when fetched by any command (whatsnew, get, list, thread). Cache is transparent to the agent — subsequent reads hit cache first.

**Storage:** Directory-based under `~/.config/ts4k/cache/`:
- `index.json` — header-only dict keyed by prefixed msg ID. Supports fast listing/filtering without loading bodies. Format: `{msg_id: {from, subject, date, source, thread_id, cached_at}}`
- `bodies/` — one `.json` file per message with the full normalized body. Named by URL-safe msg ID (colons → underscores). Only written when body is available (get/thread, not listing-only fetches).

Index stays fast even with 50k entries (~5-10MB). Bodies are loaded on demand.

### Design Decisions (from Phase 4a design review)

**WhatsApp: excluded from caching.** The WhatsApp adapter reads from a local SQLite DB via whatsapp-mcp — data is already local and instant to re-read. Caching would just duplicate it. Only adapters that make network round-trips (Gmail, O365) participate in the cache. Caching is opt-in per adapter.

**Cache stores normalized data, not raw upstream responses.** By the time a message reaches the formatting step, it's already been through the full normalize pipeline (HTML strip, reply chain dedup, signature removal, header normalization). We cache this normalized dict — it's smaller and immediately usable. The trade-off is that if the normalizer improves, cached data is stale. We handle this with schema versioning (see below).

**Schema versioning for invalidation.** The cache index carries a `schema_version` in its metadata. When normalize logic, ID generation, or cache format changes, the version is bumped. Entries written under an older schema version are treated as cache misses — the message is re-fetched from the upstream adapter, re-normalized, and the cache entry is overwritten. This avoids serving stale normalized output after pipeline improvements.

**Cleanup strategy:**
- `cache clear` — purge all cached data (or `--source g` for one source)
- `cache clear --stale` — purge only entries below the current schema version
- `cache stats` — total entries, disk size, breakdown by source, oldest/newest entry, count of stale entries
- Age-based eviction is deferred (not needed for 4a; disk is cheap, explicit commands suffice)

### Files

| File | Action |
|------|--------|
| `src/ts4k/state/cache.py` | **Create** — cache index + body read/write, query helpers |
| `src/ts4k/commands.py` | **Modify** — write-through on get_message/get_thread/whatsnew/list; read-through on get_message |
| `src/ts4k/cli.py` | **Modify** — add `ts4k cache stats\|clear` subcommand |
| `src/ts4k/server.py` | **Modify** — add `ts4k_cache` tool |
| `tests/test_cache.py` | **Create** — unit tests for cache state module |

### API (`state/cache.py`)

```python
# Write
def store_header(msg_id: str, header: dict) -> None
def store_body(msg_id: str, body: str) -> None
def store_message(msg_id: str, msg: dict) -> None  # header + body

# Read
def get_header(msg_id: str) -> dict | None
def get_body(msg_id: str) -> str | None
def has(msg_id: str) -> bool

# Query
def list_headers(source: str | None, since: str | None, contact: str | None) -> list[dict]
def count() -> int
def stats() -> dict  # total entries, total bytes, by source

# Admin
def clear(source: str | None = None) -> int  # returns count cleared
```

### Integration with commands.py

- `get_message()`: check cache first → if hit, format from cache, skip adapter. On miss, fetch from adapter and cache.
- `get_thread()`: cache each message in the thread.
- `whatsnew()` / `list_messages()`: cache all fetched messages (headers always, bodies when available).
- New `manage_cache(action, source)` function for stats/clear.

---

## Phase 4b: Adapter Pagination + Resumable Batch

**Goal:** Iterate through large mailboxes page by page, checkpoint progress, resume after interruption.

### Adapter Pagination

Extend `BaseAdapter` and each concrete adapter with optional `page_token` support:

```python
# base.py — add to list_messages signature
async def list_messages(
    self, query: str | None = None, count: int = 20,
    page_token: str | None = None,
) -> list[dict]:
```

Return value already has `_next_page_token` on the last entry (Gmail does this today at `gmail.py:125`). Standardize: if there's a next page, the returned list's metadata includes it.

- **Gmail**: Pass `page_token` as upstream param. Already extracts `_next_page_token`.
- **WhatsApp**: Use the oldest message's timestamp as cursor (timestamp-based pagination).
- **O365**: Use `$skip` or OData `@odata.nextLink`.

### Batch State (`state/batch.py`)

New state file `~/.config/ts4k/batch.json`:
```json
{
  "jobs": {
    "job-1708600000": {
      "source": "g",
      "query": "from:alice@example.com",
      "status": "in_progress",
      "pages_fetched": 5,
      "messages_cached": 247,
      "next_page_token": "token_xyz",
      "started_at": "2026-02-22T10:00:00Z",
      "updated_at": "2026-02-22T10:05:00Z"
    }
  }
}
```

### Preload Command

```
ts4k preload --source g --query "from:alice@example.com"
ts4k preload --source g --since 2020-01-01
ts4k preload --status              # show all jobs
ts4k preload --resume job-170...   # resume interrupted job
```

Runs synchronously by default (paginated loop), caches each page. Checkpoints after every page so it's crash-safe.

### Files

| File | Action |
|------|--------|
| `src/ts4k/adapters/base.py` | **Modify** — add `page_token` param |
| `src/ts4k/adapters/gmail.py` | **Modify** — pass page_token upstream |
| `src/ts4k/adapters/whatsapp.py` | **Modify** — timestamp-based pagination |
| `src/ts4k/adapters/o365.py` | **Modify** — skip-based pagination |
| `src/ts4k/state/batch.py` | **Create** — job state tracking |
| `src/ts4k/commands.py` | **Modify** — add `preload()` command |
| `src/ts4k/cli.py` | **Modify** — add preload subcommand |
| `src/ts4k/server.py` | **Modify** — add `ts4k_preload` tool |
| `tests/test_batch.py` | **Create** |

---

## Phase 4c: Overview Command

**Goal:** `ts4k o` — hierarchical summary that lets the agent drill down without fetching bodies.

### Design

Overview aggregates metadata (from cache + live adapters) into a structured summary:

```
ts4k o                                    # top-level: counts by source
ts4k o --source g                         # one source: top senders/threads
ts4k o --contact alice                    # one contact across sources
ts4k o --contact alice --period 2025      # drill into year
ts4k o --contact alice --period 2025-Q1   # drill into quarter
```

Output (pipe format):
```
Overview: 3 sources, 1,247 messages cached
g|gmail|823 msgs|2020-01..2026-02|top: alice(142), bob(89), ...
w|whatsapp|312 msgs|2024-06..2026-02|top: Family(98), Work(67), ...
o|o365|112 msgs|2025-01..2026-02|top: hr@company(34), ...
---
Drill: ts4k o --source g | ts4k o --contact alice
```

### Data Sources

1. **Cache index** — fast, already-fetched data (primary for bulk)
2. **Live adapter queries** — for "what's new since cache" (optional, `--live` flag)

Default: cache only (instant). `--live` merges with fresh adapter data.

### Files

| File | Action |
|------|--------|
| `src/ts4k/commands.py` | **Modify** — add `overview()` function |
| `src/ts4k/core/format.py` | **Modify** — add `format_overview()` |
| `src/ts4k/cli.py` | **Modify** — add `o` / `overview` subcommand |
| `src/ts4k/server.py` | **Modify** — add `ts4k_overview` tool |
| `tests/test_overview.py` | **Create** |

---

## Phase 4d: Background Tasks

**Goal:** Run preload jobs without blocking the agent.

### Design

Simple: `ts4k preload --bg` returns a job ID immediately. The job runs in a subprocess. Status via `ts4k preload --status` or `ts4k st` (which already shows jobs).

Implementation: `subprocess.Popen` running `ts4k preload --source g --query ... --job-id <id>`. The preload command already checkpoints per-page, so the subprocess is just a wrapper.

### Files

| File | Action |
|------|--------|
| `src/ts4k/commands.py` | **Modify** — add `--bg` handling to preload |
| `src/ts4k/state/batch.py` | **Modify** — add PID tracking for background jobs |
| `tests/test_batch.py` | **Modify** — add background job tests |

---

## Implementation Order

We build and ship each sub-phase as a separate commit:

1. **Phase 4a**: Cache layer + integration + tests
2. **Phase 4b**: Pagination + batch + preload command
3. **Phase 4c**: Overview command
4. **Phase 4d**: Background task wrapper

## Verification (Phase 4a)

1. `pytest` — all existing + new cache tests pass
2. `ts4k g g:<id>` — first call fetches from adapter, second call hits cache
3. `ts4k cache stats` — shows cached message count and size
4. `ts4k cache clear` — clears cache, next read goes to adapter
5. MCP: `ts4k_cache` tool works via server

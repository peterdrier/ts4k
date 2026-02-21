# RIP Phase 4: Architecture & Technical Options

**Project:** ts4k (Token Saver 4000)
**Date:** 2026-02-21
**Participants:** Test User, Claude (facilitator)

---

## Key Clarification

**ts4k is always self-hosted, single-user.** The 100k-user goal is about adoption (100k people each running their own instance), not about hosting a service. Every instance serves exactly 1 user with their own mail. No multi-tenancy, no service hosting, no OAuth delegation. User count is always 1.

---

## Architecture Decision Records

### ADR-1: Technology Stack — Python

- **Status:** Decided
- **Context:** Need a language for ts4k core. Three day-one upstream connectors: Gmail MCP (Python), WhatsApp MCP (Python), O365 MCP (TypeScript). Core value is HTML normalization pipeline.
- **Options considered:**
  - **Python** — 2/3 upstream connectors are Python (direct import possible), best HTML processing libraries (beautifulsoup4, html2text, lxml), official MCP SDK, `pipx`/`uv tool install` for clean distribution
  - **TypeScript** — O365 connector is TypeScript, official MCP SDK, good ecosystem, but Gmail/WA would require subprocess calls
  - **Go** — single binary distribution (zero deps), fastest startup, but community-only MCP SDK, weak HTML processing, all connectors are subprocess calls
- **Decision:** Python (latest LTS, currently 3.12)
- **Rationale:** Two of three upstream connectors are Python, enabling direct import as an optimization. HTML processing is ts4k's core value prop — Python has the best ecosystem for it. Official MCP SDK. Users running upstream Python MCPs already have Python installed.
- **Consequences:** Users need Python runtime (mitigated by pipx/uv). CLI startup ~300ms (acceptable). Async via asyncio for parallel adapter calls.
- **Revisit trigger:** If the adapter ecosystem shifts heavily toward TypeScript/Go, or if startup latency becomes a problem for agent workflows.

### ADR-2: Upstream Connector Integration — MCP Client

- **Status:** Decided
- **Context:** ts4k wraps existing MCP servers (Gmail, WhatsApp, O365). Need to decide how ts4k talks to them.
- **Options considered:**
  - **MCP Client** — ts4k acts as an MCP client, connects to upstream MCPs via stdio/HTTP. Clean protocol boundary. Hot-swappable. Language-agnostic.
  - **Direct library import** — For Python MCPs, import their code directly. Fastest, no process overhead. But tight coupling, version conflicts, only works for same-language.
  - **Subprocess/CLI** — Shell out to each upstream tool. Universal but fragile text parsing, slower, loses structured data.
- **Decision:** MCP Client as the primary pattern. Direct import as optional optimization for Python-native connectors when performance justifies it.
- **Rationale:** Clean adapter interface — each adapter is "connect to this MCP server and call these tools." Hot-swappable connectors (swap Gmail MCP without touching ts4k code). Works for any upstream language. The MCP protocol is the natural integration point since all connectors already speak it.
- **Consequences:** Must manage MCP server lifecycle (start/stop upstream servers or connect to already-running ones). Extra process overhead per adapter (acceptable for single-user). Adapter interface maps naturally to MCP tool calls.
- **Revisit trigger:** If MCP client overhead is measurable in common workflows (e.g., adding 500ms+ to whatsnew).

### ADR-3: State Storage — JSON Files

- **Status:** Decided
- **Context:** ts4k needs to persist watermarks, contact identity map, filter config, and efficiency stats. Single-user, single-process deployment.
- **Options considered:**
  - **JSON files** — Human-readable, LLM-readable, git-friendly, hand-editable. No concurrency concern with single user.
  - **SQLite** — Queryable, good for stats aggregation. But overkill for config, not hand-editable.
  - **Hybrid** — JSON for config, SQLite for stats. Two mechanisms to maintain.
- **Decision:** JSON for everything. Store in `~/.config/ts4k/` (XDG-compliant on Linux, cross-platform via Python's platformdirs or equivalent).
- **Rationale:** Single user means no concurrent access concern. JSON is readable and editable by humans, LLMs, and scripts. Diffable and git-committable. Simple to implement and debug.
- **Consequences:** Stats aggregation queries (e.g., "daily message counts for 30 days") require reading and parsing JSON. Fine at single-user scale. If stats get complex, migrate to SQLite later — the interface can stay the same.
- **Revisit trigger:** If stats querying becomes a pain point or if state files grow large enough to impact read/write performance.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        ts4k                                  │
│                                                              │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐               │
│  │    CLI    │  │ MCP Server│  │   Skill   │               │
│  │  (direct) │  │  (tools)  │  │(ts4k skill)│               │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘               │
│        └───────────────┼───────────────┘                     │
│                        ▼                                     │
│              ┌─────────────────┐                             │
│              │  Command Router  │                             │
│              │  wn/g/t/l/o/s/d │                             │
│              └────────┬────────┘                             │
│                       ▼                                      │
│  ┌──────────────────────────────────────────┐               │
│  │            Core Pipeline                  │               │
│  │  ┌──────────┐ ┌────────┐ ┌────────────┐ │               │
│  │  │Normalize │→│ Filter │→│   Format   │ │               │
│  │  │(HTML,    │ │(skip,  │ │(pipe-delim,│ │               │
│  │  │ dedup,   │ │ allow, │ │ mini XML,  │ │               │
│  │  │ collapse)│ │ trivial│ │ summary)   │ │               │
│  │  └──────────┘ └────────┘ └────────────┘ │               │
│  └──────────────────┬───────────────────────┘               │
│                     ▼                                        │
│  ┌──────────────────────────────────────────┐               │
│  │          Adapter Layer (MCP Client)       │               │
│  │  ┌────────┐ ┌──────────┐ ┌────────────┐ │               │
│  │  │ Gmail  │ │ WhatsApp │ │   O365     │ │               │
│  │  │Adapter │ │ Adapter  │ │  Adapter   │ │               │
│  │  └───┬────┘ └────┬─────┘ └─────┬──────┘ │               │
│  └──────┼───────────┼─────────────┼─────────┘               │
│         ▼           ▼             ▼                          │
│  ┌──────────────────────────────────────────┐               │
│  │              State (JSON)                 │               │
│  │  watermarks.json | contacts.json          │               │
│  │  config.json     | stats.json             │               │
│  │  ~/.config/ts4k/                          │               │
│  └──────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────┘
         │              │              │
         ▼              ▼              ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │  Google   │  │ WhatsApp │  │  O365    │
   │ Workspace │  │   MCP    │  │   MCP    │
   │   MCP     │  │(Go+Py)  │  │  (TBD)   │
   └──────────┘  └──────────┘  └──────────┘
```

---

## Data Flow

### whatsnew (typical daily use)

```
Agent calls: ts4k wn
  → Command Router dispatches to whatsnew handler
  → Handler reads watermarks.json for last-seen timestamps
  → Handler calls all enabled adapters in parallel (asyncio):
      Gmail adapter → MCP client → google_workspace_mcp → search_gmail_messages(after:timestamp)
      WhatsApp adapter → MCP client → whatsapp-mcp → list_messages(since:timestamp)
      O365 adapter → MCP client → o365-mcp → list-mail-messages(since:timestamp)
  → Raw results from each adapter enter Core Pipeline:
      Normalize: strip HTML, dedup, collapse whitespace
      Filter: apply skip lists, category filters, trivial message filter
      Format: pipe-delimited listing with source prefix, contact, thread ID, count, date, size
  → Watermarks updated in watermarks.json
  → Stats updated in stats.json (messages in, bytes in, bytes out)
  → Formatted output returned to agent
```

### get (single message read)

```
Agent calls: ts4k g g:msg_abc123
  → Command Router parses source prefix "g:" → Gmail adapter
  → Gmail adapter → MCP client → get_gmail_message_content(msg_abc123)
  → Raw message enters Core Pipeline:
      Normalize: strip HTML tags (preserve hrefs), remove tracking pixels,
                 remove signatures, strip quoted reply chains, collapse whitespace
      Filter: (not applied to explicit reads)
      Format: mini XML with metadata attributes
  → Stats updated
  → Formatted output returned to agent
```

### overview (bulk historical drill-down)

```
Agent calls: ts4k o alice@acme.com
  → Command Router dispatches to overview handler
  → Handler resolves "alice@acme.com" via contacts.json (may map to multiple platforms)
  → Handler calls relevant adapters:
      Gmail: search for all messages from/to alice@acme.com (metadata only, paginated)
      WhatsApp: list_messages for alice's WhatsApp contact
      O365: search for alice across O365 mailboxes
  → Aggregate metadata: total count, date range, thread count, per-year/month counts
  → Format: structured summary with drill-down hints (suggested next commands)
  → Return to agent (cheap — metadata only, no message bodies fetched)
```

---

## Adapter Interface

Each adapter implements a common interface. Adapters are thin — they translate ts4k operations into MCP tool calls on the upstream server.

```python
class BaseAdapter:
    """Base interface for ts4k adapters. All methods return normalized dicts,
    not formatted output. Formatting is done by the Core Pipeline."""

    async def search(self, query: str, since: str = None, count: int = 20) -> list[dict]:
        """Search/list messages matching criteria. Returns metadata dicts."""

    async def get_message(self, msg_id: str) -> dict:
        """Fetch single message. Returns dict with metadata + raw content."""

    async def get_thread(self, thread_id: str) -> dict:
        """Fetch conversation/thread. Returns dict with messages list."""

    async def get_overview(self, contact: str = None, period: str = None) -> dict:
        """Aggregate metadata for overview. Returns counts, date ranges, threads."""

    async def send(self, to: str, content: str, **opts) -> dict:
        """Send message via platform. Optional, adapter-dependent."""

    async def draft(self, to: str, content: str, **opts) -> dict:
        """Create draft. Optional, adapter-dependent."""

    async def health(self) -> dict:
        """Check adapter connectivity and status."""
```

Note: This is illustrative, not final. The actual interface will be refined during implementation based on what the upstream MCPs actually return.

---

## Technical Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **WhatsApp Web protocol (whatsmeow) breaks** | Medium | High — WhatsApp adapter becomes unusable | Fork-friendly stance. Community usually patches within days. WhatsApp adapter failure is isolated from others. |
| **Upstream MCP API changes** | Low-Medium | Medium — adapter needs updating | Fork upstream repos. Pin versions. Adapter layer isolates changes. |
| **HTML normalization quality** | Medium | High — bad normalization = bad token savings = ts4k's value prop is gone | This is the prototype priority. Test against real mail data early. Iterate aggressively. |
| **MCP client lifecycle management** | Medium | Medium — flaky connections to upstream MCPs | Need robust connection handling: retry, timeout, health checks. Already-running vs on-demand server management. |
| **O365 connector unknown** | Medium | Medium — day-two adapter depends on evaluating and integrating a new upstream MCP | Research done (Softeria recommended). Integration is day-two, not blocking. |
| **Overview command performance on large mailboxes** | Medium | Medium — 15 years of Gmail metadata could be slow to aggregate | Paginate upstream queries. Cache overview results. Incremental updates. |
| **Python startup latency for CLI** | Low | Low — 300ms is acceptable for agent use | Monitor. If it becomes a problem, consider persistent daemon or socket mode. |

---

## Deferred Decisions

| Decision | When to Revisit |
|----------|----------------|
| O365 MCP connector choice (Softeria vs elyxlz vs fork) | Before starting O365 adapter implementation |
| Stats migration from JSON to SQLite | If stats queries become a pain point |
| Output format specifics (exact pipe-delimited schema, XML tag names) | Phase 5 prototyping |
| Adapter interface final contract | During first adapter implementation |
| Packaging (PyPI, pipx, Docker, etc.) | Before first public release |
| `ts4k skill` output format | During skill/help implementation |

---

## Decisions Made in Phase 4

| # | Decision | Rationale |
|---|----------|-----------|
| 22 | Python (latest LTS, 3.12) | 2/3 upstream connectors are Python. Best HTML processing ecosystem. Official MCP SDK. |
| 23 | MCP Client for upstream integration | Clean protocol boundary. Hot-swappable. Language-agnostic. Direct import as optional optimization. |
| 24 | JSON for all state in `~/.config/ts4k/` | Single user, no concurrency concern. Human/LLM readable. Git-friendly. Migrate stats to SQLite if needed later. |

---

## Open Questions (to resolve in later phases)

- Output format specifics: exact pipe-delimited schema, body markup format
- Adapter interface refinement: what exactly do upstream MCPs return and how do we normalize it?
- MCP server lifecycle: does ts4k start/stop upstream MCPs or connect to already-running ones?
- Connection to existing life-os setup: how does ts4k fit alongside the current `.mcp.json` configuration?
- Packaging approach for public distribution

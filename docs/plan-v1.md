# ts4k — Token Saver 4000

## Purpose

ts4k is a unified messaging gateway that gives LLM agents (Claude Code, OpenClaw, custom pipelines) token-efficient access to messages across platforms — Gmail, WhatsApp, Telegram, and future sources. It sits between raw messaging APIs and AI consumers, solving two problems simultaneously: **platform fragmentation** (each source has its own API, auth, format, and quirks) and **token waste** (raw message data is grotesquely bloated for LLM consumption).

ts4k does not process, analyze, or act on messages. It **retrieves, normalizes, and delivers** them in formats optimized for LLM context windows. Think of it as a read-focused funnel: many verbose inputs in, one clean token-efficient output out. It delegates to existing platform-specific MCP servers or CLIs for actual API connectivity and optionally provides passthrough for sending/drafting where the underlying adapter supports it.

## Vision

An LLM agent should be able to say `ts4k whatsnew` and get a unified, cross-platform feed of recent messages — normalized, deduplicated, filtered, and formatted — in a single tool call that costs hundreds of tokens instead of tens of thousands. The agent should never see raw HTML, platform-specific JSON schemas, tracking pixels, email signatures, quoted reply chains, or any other noise that wastes context window budget without adding informational value.

ts4k is the **data access layer** for an AI-powered life management system. It enables:

- **Morning briefings** — a coordinator agent calls `ts4k whatsnew` and gets everything new across all platforms in one pipe-delimited listing, then drills into specific threads as needed
- **Contact intelligence pipelines** — batch processing scripts call `ts4k history <contact>` and get normalized, chronologically merged timelines across platforms for fact extraction
- **Interactive agents** — Claude Code calls `ts4k read <id>` and gets clean, minimal content ready for reasoning, not an 8,000-token HTML blob

The name reflects the core value proposition: every design decision optimizes for fewer tokens consumed by downstream LLM consumers.

## Architecture Principles

### 1. Funnel, Not Ocean

ts4k interfaces with existing platform adapters (MCP servers, CLIs, APIs) rather than reimplementing messaging integrations. It wraps `gog` for Gmail, future WhatsApp Bridge CLIs, Telegram Bot APIs, etc. If a good MCP server exists for a platform, ts4k delegates to it and focuses on normalization and output formatting.

### 2. Metadata First, Content on Demand

Never return content the agent didn't specifically ask for. Every command defaults to the minimum useful response:

- `ts4k whatsnew` → pipe-delimited headers with sizes (agent decides what to expand)
- `ts4k read <id>` → metadata + preview first, agent opts into `--full`, `--tail N`, `--latest`
- `ts4k history <contact>` → counts, date ranges, top threads, suggested drill-down commands

This is the database principle: you wouldn't `SELECT *` from a 1000-row table and pipe it into a prompt.

### 3. The CLI Is the Persistence Layer

When an agent calls a ts4k tool, the action happens immediately (watermarks update, state writes to disk). There is no separate "save" step the LLM must remember. Using the tool IS the side effect.

### 4. Format Is a Feature

ts4k's output formats are deliberately designed for LLM token efficiency:

- **Pipe-delimited** for flat listings (headers, whatsnew feeds) — most compact, ~1200 tokens per 100 records vs ~3000 for verbose XML
- **Mini XML** for message bodies where content may contain delimiters — `<e id="..." from="..." date="...">body</e>`
- **Mixed formats** with clear boundary markers when combining listings with content
- **Newlines** for structure (they aid parsing), **no indentation** (wasted tokens)
- **Summaries at top** of output (leverages recency bias in attention)

### 5. Normalize Everything

Every message, regardless of source, passes through the same preprocessing pipeline before reaching the LLM:

1. Strip HTML tags (preserve href values, convert br/p to newlines)
2. Remove tracking pixels and 1x1 images
3. Remove email signatures (---, "Sent from iPhone", confidentiality notices)
4. Strip quoted reply chains ("> " prefixed, "On {date} {person} wrote:")
5. Collapse whitespace
6. Remove unsubscribe blocks and footer boilerplate
7. Convert HTML tables to pipe-delimited format (preserves structure)

Raw HTML email: ~8,000 tokens → after normalization: ~400 tokens. A 20x reduction.

### 6. Filter Before the LLM Sees It

Multi-layer noise filtering eliminates 80-85% of messages before any LLM processing:

- **Layer 1**: Platform spam filters (Gmail's built-in)
- **Layer 2**: Category labels (skip PROMOTIONS, UPDATES, SOCIAL, FORUMS — eliminates 60-70%)
- **Layer 3**: Skip lists (noreply@, notifications@, automated senders)
- **Layer 4**: Contact allowlist (only process known contacts for profiling use cases)
- **Layer 5**: Trivial message filter (<20 chars, attachment-only, forwarded-only)

Filter stats are tracked for tuning: `{"total_new": 127, "processed": 15}`.

## Operational Modes

### CLI Mode

Direct command-line usage for scripts, cron jobs, and interactive development:

```bash
ts4k whatsnew                              # all platforms, since last watermark
ts4k whatsnew --source gmail               # single platform
ts4k whatsnew --since 2026-02-20T08:30:00Z # explicit timestamp

ts4k list --from alice@acme.com --count 20 # headers for a contact
ts4k read <id>                             # normalized single message
ts4k read <id> --meta                      # metadata + preview only
ts4k read <id> --full                      # full normalized content
ts4k read <id> --tail 5                    # last 5 messages in thread
ts4k read <id> --latest                    # most recent reply only
ts4k read <id> --save output.txt           # dump to file, return confirmation only

ts4k thread <id>                           # full normalized thread
ts4k thread <id> --strip-trivial           # remove "ok", "thanks", scheduling noise

ts4k history <contact>                     # summary: counts, date range, top threads
ts4k history <contact> --recent 20         # last 20 messages, headers only
ts4k history <contact> --year 2025         # all 2025 messages, headers only
ts4k history <contact> --thread <id>       # specific thread
ts4k history <contact> --search "contract" # keyword search
ts4k history <contact> --grep "budget" --count  # count matches without fetching

ts4k contacts                              # list known contacts with platform mappings
ts4k status                                # watermarks, filter stats, adapter health
```

### MCP Server Mode

Exposes the same commands as MCP tools for Claude Code, Claude Desktop, or any MCP-compatible agent:

```
Tools:
  ts4k_whatsnew     — unified cross-platform new message feed
  ts4k_list         — filtered message headers for a contact/query
  ts4k_read         — single message with normalization options
  ts4k_thread       — full thread, normalized and deduplicated
  ts4k_history      — contact history summary with drill-down hints
  ts4k_contacts     — contact identity map
  ts4k_status       — system health and watermark state
  ts4k_send         — passthrough to underlying adapter (if supported)
  ts4k_draft        — passthrough to underlying adapter (if supported)
```

MCP mode is the primary integration path for interactive agents. When an agent calls `ts4k_whatsnew`, the response is already formatted for minimal token consumption — the agent can reason over it immediately.

## Output Formats

### Whatsnew / List (pipe-delimited)

```
SOURCE | CONTACT | THREAD_ID | MSG_COUNT | LATEST | SIZE
gmail | alice@acme.com | g_thr_abc | 3 | 2026-02-20T09:15:00Z | 2kb
whatsapp | +31612345678 | w_chat_alice | 5 | 2026-02-20T09:30:00Z | 1kb
telegram | @bob_t | t_chat_bob | 1 | 2026-02-20T08:50:00Z | 500b
```

With size hints, the agent can make smart fetch decisions. 48kb thread? Fetch `--tail 5`. 1kb message? Fetch `--full`.

### Message Headers (pipe-delimited)

```
FROM | SUBJECT | DATE | ID | SIZE | MSGS
alice@acme.com | Meeting tomorrow | 2026-02-19 | 18d4a2b3c4e5f6a7 | 2kb | 1
bob@corp.com | Q3 Report Draft | 2026-02-18 | 18d4a2b3c4e5f6a8 | 48kb | 23
```

### Message Bodies (mini XML)

```xml
<e id="18d4a2b3c4e5f6a7" from="alice@acme.com" subject="Meeting tomorrow" date="2026-02-19">
Hey, are we still on for 3pm? Let me know if the conference room changed.
</e>
```

### Thread (normalized, sequential)

```
THREAD: Vendor Contract Negotiation
PARTICIPANTS: user@example.net, alice@acme.com, bob@legal.com
MESSAGES: 15
DATE_RANGE: 2025-06-01 to 2025-06-14
SIZE: 4kb (normalized)

[1] 2025-06-01 user@example.net
Kicking off vendor contract review. Key concerns: payment terms, liability cap, renewal clause.

[2] 2025-06-02 alice@acme.com
Agreed on those three. I'd add IP ownership — their current language is too broad.
```

### History Summary (structured metadata)

```
CONTACT: alice@acme.com
TOTAL_MESSAGES: 347
DATE_RANGE: 2023-03-15 to 2026-02-20
THREADS: 89

FREQUENCY:
  2026: 82 messages
  2025: 145 messages
  2024: 98 messages

TOP_THREADS:
  Q3 Planning (23 msgs, 48kb) | thread_abc123
  Vendor Contract (15 msgs, 12kb) | thread_def456

COMMANDS:
  ts4k history alice@acme.com --recent 20
  ts4k history alice@acme.com --year 2025
  ts4k history alice@acme.com --thread thread_abc123
  ts4k history alice@acme.com --search "contract terms"
```

The `COMMANDS` block is intentional — it teaches the agent what drill-down options exist, costing almost nothing in tokens.

## Adapter Architecture

ts4k does not implement messaging platform APIs directly. It wraps existing tools through a uniform adapter interface:

```
┌─────────────────────────────────────────────┐
│                   ts4k                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ Normalize │ │  Filter  │ │  Format  │    │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘    │
│       └─────────┬──┘            │           │
│           ┌─────┴─────┐        │           │
│           │  Adapter   │        │           │
│           │  Interface │        │           │
│           └─────┬─────┘        │           │
│    ┌────────┬───┴───┬────────┐ │           │
│    ▼        ▼       ▼        ▼ │           │
│ ┌──────┐┌──────┐┌──────┐┌──────┐          │
│ │Gmail ││WhatsA││Telegr││Future│          │
│ │Adapt.││Adapt.││Adapt.││Adapt.│          │
│ └──┬───┘└──┬───┘└──┬───┘└──┬───┘          │
└────┼───────┼───────┼───────┼────────────────┘
     ▼       ▼       ▼       ▼
   gog CLI  WA MCP  TG API  ...
```

Each adapter implements:

```python
class BaseAdapter:
    def whatsnew(self, since: str) -> list[dict]:
        """New activity since timestamp, as normalized records"""

    def list_messages(self, contact: str, **filters) -> list[dict]:
        """Message headers matching filters"""

    def read_message(self, msg_id: str) -> str:
        """Single normalized message content"""

    def read_thread(self, thread_id: str) -> str:
        """Full normalized thread content"""

    def history(self, contact: str) -> dict:
        """Contact history summary with metadata"""

    def send(self, to: str, content: str, **opts) -> dict:
        """Passthrough send (optional, adapter-dependent)"""
```

Platform failures are isolated — if WhatsApp is down, Gmail and Telegram results still return.

## State Management

### Watermarks

ts4k tracks per-platform, per-contact processing timestamps:

```json
{
  "watermarks": {
    "gmail": "2026-02-20T08:30:00Z",
    "whatsapp": "2026-02-19T22:15:00Z",
    "telegram": "2026-02-20T01:00:00Z"
  }
}
```

`ts4k whatsnew` with no arguments reads these watermarks, fetches deltas, and updates them on completion.

### Contact Identity Map

Cross-platform identity resolution via manually maintained JSON:

```json
{
  "alice_chen": {
    "display_name": "Alice Chen",
    "gmail": "alice@acme.com",
    "whatsapp": "+31612345678",
    "telegram": "@alicechen"
  }
}
```

Start manually for top 20 contacts. Auto-resolution is unreliable and not worth early complexity.

### Filter Configuration

Configurable skip lists and allowlists per adapter:

```json
{
  "gmail": {
    "skip_labels": ["CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_SOCIAL"],
    "skip_senders": ["*@noreply.*", "*@notifications.*", "*@github.com"],
    "skip_subjects": ["Out of Office*", "Automatic reply*"]
  }
}
```

### IDs

ts4k uses native platform IDs (Gmail hex IDs, WhatsApp message IDs, etc.) prefixed with source for cross-platform uniqueness: `g:18d4a2b3c4e5f6a7`, `w:msg_456`, `t:msg_789`. Native IDs are globally unique and persistent across sessions — no synthetic sequential IDs that break when the inbox changes between agent turns.

## Token Efficiency Budget

Target improvements over raw MCP/API responses:

| Scenario | Raw API/MCP | ts4k | Savings |
|---|---|---|---|
| 100 email headers | ~3000 tokens (JSON) | ~1200 tokens (pipe) | 60% |
| Single HTML email | ~8000 tokens | ~400 tokens | 95% |
| 15-msg thread with quoted replies | ~12000 tokens | ~2000 tokens | 83% |
| Whatsnew across 3 platforms | ~5000+ tokens (3 calls) | ~800 tokens (1 call) | 84% |

These are conservative estimates. The combination of HTML stripping, reply chain dedup, noise filtering, and compact formatting compounds to massive savings.

## Technology Decisions

- **Language**: Python (broad library support for html2text, email parsing, adapters; runs on NUC Linux natively)
- **MCP SDK**: Official Python MCP SDK for server mode
- **HTML processing**: `html2text` or `beautifulsoup4` with custom rules
- **State storage**: JSON files, git-committed, human-readable
- **Configuration**: JSON or TOML config files in `~/.config/ts4k/`
- **Packaging**: pip-installable with CLI entry point + MCP server entry point
- **Deployment**: Direct install on NUC, or Docker container

## Downstream Integration

ts4k is the data layer. It does not analyze or act on messages. Downstream consumers include:

- **Contact intelligence pipeline** — calls `ts4k history` and `ts4k thread` to feed Haiku extraction → Sonnet synthesis → contact profiles
- **Morning briefing agent** — calls `ts4k whatsnew`, reads task files, produces narrative summary
- **Interactive Claude Code** — calls ts4k MCP tools during conversation to answer questions about messages
- **Batch processing scripts** — call ts4k CLI for historical backload at throttled pace (1 thread/min)

## Implementation Phases

### Phase 1: Gmail Adapter + Core

- `ts4k whatsnew` (Gmail only)
- `ts4k list`, `ts4k read`, `ts4k thread` with normalization
- Pipe-delimited and mini XML output formats
- HTML stripping and reply chain dedup
- Watermark state management
- CLI mode only
- Test against real Gmail data with one contact

### Phase 2: MCP Server + History

- MCP server mode wrapping CLI commands
- `ts4k history` with drill-down commands
- Filter configuration (skip lists, contact allowlist)
- Filter stats tracking
- `ts4k contacts` and `ts4k status`

### Phase 3: Multi-Platform

- WhatsApp adapter
- Telegram adapter
- Cross-platform whatsnew merge
- Contact identity map integration
- Source-prefixed IDs

### Phase 4: Polish + Send Passthrough

- `ts4k send` / `ts4k draft` passthrough where adapters support it
- `--strip-trivial` for thread noise reduction
- `--grep` and `--count` for search without fetching
- Pagination for large result sets
- Performance optimization for batch use cases

## Key Design Constraints

1. **Read-heavy by design** — ts4k is primarily a read/query tool. Send/draft are passthrough conveniences, not core features.
2. **No LLM calls inside ts4k** — ts4k is the tool the LLM calls, not a tool that calls LLMs. All intelligence belongs to the consuming agent.
3. **Adapter-agnostic output** — downstream consumers never see platform-specific data. A WhatsApp message and a Gmail message look identical after normalization.
4. **Fail gracefully** — one adapter failure never blocks others. Partial results are better than no results.
5. **Stateless reads, stateful watermarks** — reads are idempotent. Only `whatsnew` (and its watermark advancement) has side effects.
6. **Git-friendly state** — all config and state files are text, diffable, committable.

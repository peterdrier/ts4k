# RIP Phase 3: Scope & Requirements

**Project:** ts4k (Token Saver 4000)
**Date:** 2026-02-21
**Participants:** Test User, Claude (facilitator)

---

## Connector Surface Area Analysis

Before defining ts4k's command surface, we mapped what the three day-one upstream connectors actually expose:

### Upstream Connector Inventory

| Connector | Type | Source | Tools |
|-----------|------|--------|-------|
| Gmail (Google Workspace) | Python FastMCP | user/google_workspace_mcp (fork) | search, read single, batch read, thread, labels, draft, send, attachments, filters |
| WhatsApp | Go bridge + Python FastMCP | whatsapp-mcp (local) | search contacts, list messages, list chats, get chat, get direct chat, get contact chats, last interaction, message context, history sync, download media |
| O365 (TBD) | TypeScript (recommended: Softeria/ms-365-mcp-server) | TBD | list mail, get mail, send, draft, search, folders, move, delete, calendar |

### O365 Connector Evaluation

| Option | Lang | Stars | Last Commit | Email Tools | Verdict |
|--------|------|-------|-------------|-------------|---------|
| **Softeria/ms-365-mcp-server** | TypeScript | 474 | 6 days ago | 8 + search | Best maintained, broadest, `--read-only` and TOON output modes |
| elyxlz/microsoft-mcp | Python | 39 | 8 months ago | 11 | Python-native but stale, could fork |
| ryaker/outlook-mcp | JavaScript | 249 | 4 months ago | 4 | Too thin on email tools |

Also watching: marlinjai/email-mcp (unified Gmail+Outlook+IMAP, 0 stars, created Feb 2026 — conceptually similar to ts4k).

### Capability Intersection

| Operation | Gmail | WhatsApp | O365 | ts4k Command |
|-----------|-------|----------|------|-------------|
| Search/list messages | search_gmail_messages | list_messages, list_chats | list-mail-messages, search-query | `list` / `l` |
| Read single message | get_gmail_message_content | list_messages (filtered) | get-mail-message | `get` / `g` |
| Read batch | get_gmail_messages_content_batch | — | — | (ts4k synthesizes from single reads) |
| Read thread/conversation | get_gmail_thread_content | get_message_context | — (no thread tool) | `thread` / `t` |
| Search contacts | via search query | search_contacts | via contacts tools | `contacts` / `c` |
| Download attachment/media | get_gmail_attachment_content | download_media | via message content | (via `get` with flags) |
| Send | send_gmail_message (disabled) | removed (security) | send-mail | `send` / `s` |
| Draft | draft_gmail_message | — | create-draft-email | `draft` / `d` |
| Labels/folders | labels, modify_labels | — | folders, move-mail | (future) |
| History sync/backload | via search with date ranges | request_history_sync | via search with date ranges | `overview` / `o` + batch |
| Last interaction | — | get_last_interaction | — | (via `list` with contact filter) |

---

## Command Surface

All commands support single-letter aliases. LLM help (`ts4k skill`) points to short forms; human help (`ts4k help`) uses long forms.

| Command | Alias | What It Does | Priority |
|---------|-------|-------------|----------|
| `whatsnew` | `wn` | Unified feed of new activity since last check, across all platforms | Must-have |
| `get` | `g` | Read single message, normalized. Flags: `--full`, `--meta`, `--summary` | Must-have |
| `thread` | `t` | Read conversation/thread, normalized. Flags: `--summary`, `--latest N` | Must-have |
| `list` | `l` | Search/filter messages. Flags: `--from`, `--since`, `--source`, `--count` | Must-have |
| `overview` | `o` | Hierarchical summary — counts, periods, people, threads. Drill-down with `--period`, `--contact` | Must-have |
| `contacts` | `c` | Cross-platform identity map. Create/link/query. | Must-have |
| `send` | `s` | Send via appropriate platform (safety-railed) | Should-have |
| `draft` | `d` | Create draft via appropriate platform | Should-have |
| `status` | `st` | Adapter health, watermarks, stats, efficiency metrics | Should-have |
| `help` | `h` | Human-readable help | Must-have |
| `skill` | — | LLM-optimized command reference (self-documenting, always current) | Must-have |

### The Drill-Down Pattern (Bulk Historical)

The `overview` command enables hierarchical exploration of massive mailboxes:

```
Agent: ts4k o alice@acme.com
→ 2,847 messages | 2011-01 to 2026-02 | 347 threads | 15.2 GB raw
  2011: 42 msgs | 2012: 189 msgs | 2013: 312 msgs | ... | 2025: 287 msgs
  Top threads: Contract Renewal (89 msgs), Q3 Planning (67 msgs), ...
  Heaviest months: 2014-07 (94 msgs), 2019-03 (88 msgs), ...

Agent: ts4k o alice@acme.com --period 2014-07
→ 94 messages | 12 threads | 1.2 GB raw
  Vendor Renegotiation (34 msgs, 400kb) | thr_abc123
  Office Move Planning (28 msgs, 280kb) | thr_def456
  ...

Agent: ts4k t thr_abc123 --summary
→ THREAD: Vendor Renegotiation | 34 msgs | 2014-07-03 to 2014-07-28
  PARTICIPANTS: alice@acme.com, user@example.net, bob@legal.com
  [1] 2014-07-03 peter: Kicking off vendor renegotiation...
  [2] 2014-07-03 alice: Key concerns are payment terms and...

Agent: ts4k g msg_xyz --full
→ (single normalized message, ready for Haiku extraction)
```

Each level is cheap. The LLM sees the shape, picks where to drill, and only fetches what it needs. This is what makes "$100 for 10 years of history" viable.

---

## Three Operational Modes

| Mode | How Agent Uses It | Context Cost | When To Use |
|------|-------------------|-------------|-------------|
| **MCP** | ts4k tools always loaded in agent context | ~1-2k tokens permanently | Dedicated comms agent where ts4k is the primary job |
| **CLI** | Agent calls `ts4k` via bash | 0 tokens until used, pure pipe output | Scripts, cron, agents that occasionally need comms |
| **Skill** | `/ts` loads thin stub → calls `ts4k skill` → gets current help → uses CLI | 0 tokens until invoked, self-documenting | General-purpose agents where comms is one of many capabilities |

The skill mode is the sweet spot for general-purpose agents. The Claude skill file for `/ts` is tiny and never changes — it just calls `ts4k skill` to get the current command reference. Documentation lives in ts4k itself, never drifts.

CLI is a first-class agent interface, not just developer tooling. Pipe table output is a few % more efficient than MCP JSON responses.

---

## Capability Map

| Capability | Description | Traces To | Complexity | Priority |
|-----------|-------------|-----------|------------|----------|
| **Normalize pipeline** | Strip HTML, dedup reply chains, collapse whitespace, remove signatures/tracking | Token reduction (95%) | M | Must-have |
| **whatsnew** | Unified cross-platform new activity feed | Daily efficiency headline | M | Must-have |
| **get (single message)** | Fetch + normalize single message by ID | Token reduction | S | Must-have |
| **thread** | Fetch + normalize full conversation | Token reduction | M | Must-have |
| **list (search/filter)** | Search messages across platforms with query/contact/date filters | Unified interface | M | Must-have |
| **overview** | Hierarchical summary with drill-down (counts, periods, people, threads) | Bulk historical viability | L | Must-have |
| **Adapter interface** | Generic enough for 5-8+ sources, not just messaging | Future extensibility | M | Must-have |
| **Watermark state** | Per-platform last-seen tracking, auto-advancing | Operational reliability | S | Must-have |
| **Compact output formats** | Pipe-delimited listings, minimal markup for bodies | Token reduction (60%) | S | Must-have |
| **Contact identity map** | Cross-platform person linking, LLM-managed | Contact consolidation | M | Must-have |
| **CLI mode** | Primary agent interface. Short aliases, pipe output. | Agent integration + efficiency | S | Must-have |
| **MCP server mode** | Same commands as MCP tools, for dedicated comms agents | Agent integration (always-on) | M | Must-have |
| **Skill self-description** | `ts4k skill` outputs LLM-optimized command reference | Zero-cost skill integration | S | Must-have |
| **Dual help** | `help` for humans, `skill` for LLM-optimized agent help | Usability + context savings | S | Must-have |
| **Adapter: Gmail** | Wraps google_workspace_mcp | Day-one platform | M | Must-have |
| **Adapter: WhatsApp** | Wraps whatsapp-mcp | Day-one platform | M | Must-have |
| **Adapter: O365** | Wraps TBD MS Graph MCP | Day-one platform | M | Must-have |
| **send / draft** | Passthrough to adapter with safety rails (draft-only mode, confirmation) | Bidirectional pipe | M | Should-have |
| **Efficiency stats** | Bytes in/out, tokens saved, per-platform/contact/day breakdowns | Adoption story + tuning | S | Should-have |
| **status** | Adapter health, watermarks, error state | Operational reliability | S | Should-have |
| **Filter config** | Skip lists, category filters, sender allowlists | Noise reduction | M | Should-have |
| **Batch/resumable backload** | Process large mailboxes in chunks, resume on failure | Bulk historical | L | Should-have |

### Priority Cuts

**Half the time — what survives?**
Normalize pipeline, whatsnew, get, thread, list, adapter interface, watermarks, compact output, CLI mode, skill self-description, Gmail adapter.

**Half again — absolute core?**
Normalize pipeline, whatsnew, get, CLI mode, Gmail adapter. Fetch one platform's new messages and make them small.

---

## Scope Boundary

### In Scope

- Normalize pipeline (HTML strip, reply dedup, whitespace collapse, signature removal)
- 11 commands: whatsnew, get, thread, list, overview, contacts, send, draft, status, help, skill
- Three operational modes: CLI, MCP server, Skill (self-documenting)
- Single-letter command aliases
- Three day-one adapters: Gmail, WhatsApp, O365
- Adapter interface designed for 5-8+ sources including non-messaging (calendar, Jira, GitHub, etc.)
- Pipe-delimited output for listings, minimal markup for message bodies
- Watermark state management
- Cross-platform contact identity linking (LLM-managed)
- Efficiency stats tracking
- Hierarchical drill-down for bulk historical processing
- Safety rails on send/draft (draft-only mode, confirmation flags, per-adapter config)

### Out of Scope

- Building platform connectors (wraps existing MCPs/tools only)
- OAuth / auth management (upstream connectors handle this)
- Real-time push / websocket listeners
- UI of any kind
- Message storage / database (fetches and passes through, with watermark + contact state only)
- Automatic contact identity resolution (LLM decides, ts4k stores)
- LLM calls inside ts4k (no summarization, no analysis, no intelligence)
- Calendar / Drive / non-messaging adapters (day 5+, not day 1)

### Boundary Decisions

| Decision | In/Out | Rationale |
|----------|--------|-----------|
| Send/draft | In (should-have) | Bidirectional pipe. Other users need send. Safety-railed. |
| Caching normalized output | In (for long-running ops) | Backloading voice notes, resumable batch processing |
| Contact identity map | In (LLM-managed) | ts4k stores/queries, LLM drives linking decisions |
| Non-messaging adapter support | In (architecture only) | Design the interface generically. Don't build calendar/Jira adapters yet. |
| Polling/scheduling | Out | External scheduler (cron) calls ts4k. ts4k doesn't schedule itself. |
| Message analysis/summarization | Out | ts4k is the data layer. Intelligence belongs to consuming agents. |

---

## Non-Functional Requirements

| Category | Requirement | Priority |
|----------|------------|----------|
| **Performance** | Single message get < 2s. whatsnew across 3 platforms < 5s. | Must-have |
| **Reliability** | Adapter failures isolated — one platform down doesn't block others. Partial results returned. | Must-have |
| **Cross-platform** | CLI runs on Linux (NUC), Windows, Mac. MCP server runs on Linux. | Must-have |
| **Installability** | pip install (or equivalent) + docs. Someone else can get it running. | Should-have |
| **Resumability** | Batch/backload operations can resume after failure. Watermarks track progress. | Should-have |
| **Stats** | Track bytes in/out, messages processed, tokens saved. Queryable via `status`. | Should-have |
| **Security** | Send/draft safety rails. No credentials stored by ts4k (upstream handles auth). | Must-have |

---

## Decisions Made in Phase 3

| # | Decision | Rationale |
|---|----------|-----------|
| 17 | All commands support single-letter aliases | Token savings + convenience. LLM help uses short forms, human help uses long forms. |
| 18 | Send and draft in scope with safety rails | Bidirectional pipe. Draft-only mode, confirmation flags, per-adapter config. |
| 19 | `overview` command enables hierarchical drill-down for bulk history | Summary → period → thread → message. Each level cheap. Makes "$100 for 10 years" viable. |
| 20 | Three operational modes: MCP, CLI, Skill | Agent picks based on context budget. Skill mode is sweet spot for general-purpose agents. |
| 21 | `ts4k skill` outputs LLM-optimized command reference | Self-documenting. Claude skill `/ts` is thin pointer. Docs live in ts4k, never drift. |

---

## Open Questions (to resolve in later phases)

- Stack decision: Python vs alternatives (3 options in Phase 4)
- How does ts4k talk to upstream MCPs? (MCP client? subprocess? direct import?)
- Output format specifics: what exactly does pipe-delimited look like? What markup for bodies?
- Adapter interface contract: what methods must an adapter implement?
- Contact identity schema
- How does `overview` aggregate data efficiently for 15-year mailboxes?
- Where do watermarks and contact state live? (`~/.config/ts4k/`? XDG? project-local?)

# ts4k Intentions

What ts4k aims to be, independent of what's built today.

---

## What ts4k Is

A normalizing funnel between LLM agents and communication platforms. ts4k retrieves messages from Gmail, WhatsApp, O365, and future sources, then normalizes, filters, and compactly formats them so downstream agents spend hundreds of tokens instead of tens of thousands per interaction.

One MCP server replaces N platform-specific ones. One CLI command checks all platforms at once.

**Elevator pitch:** 60-95% token reduction per interaction, unified cross-platform access, shrunk MCP/skill footprint.

**Two usage patterns:**
- **Real-time** -- daily briefings, interactive agent queries, quick check-ins
- **Bulk historical** -- data-mining years of communication history at viable cost (10 years for ~$100 instead of ~$10,000)

---

## Anti-Goals

1. **ts4k is not an AI.** No analysis, no summarization, no decision-making about content. It's the data layer, not the intelligence layer.
2. **ts4k is not a connector.** It wraps existing APIs and tools. It doesn't implement OAuth or platform protocols from scratch.
3. **ts4k is not a real-time listener.** No websockets, no push notifications. Cheap polling at short intervals is viable.
4. **ts4k is not a message database.** It fetches, normalizes, and passes through. It maintains only watermark state, contact identity links, and cache for long-running operations.
5. **No UI.** CLI + MCP server are the interfaces.
6. **ts4k doesn't own the intelligence.** It may enforce safety (draft-only, send confirmation), but what to send, who to contact, how to respond -- that's the consuming agent.

---

## Core Capabilities

These are always-intended, defining features of ts4k:

- **Normalize pipeline** -- HTML stripping, reply chain dedup, whitespace collapse, signature removal, tracking pixel removal. Raw 8000-token HTML email becomes ~400 tokens.
- **Unified cross-platform feed** -- `whatsnew` returns merged, normalized activity across all configured sources in a single call.
- **Message retrieval** -- `get` (single message), `thread` (conversation), `list` (search/filter). All with normalization and metadata-first defaults.
- **Hierarchical drill-down** -- `overview` enables cheap exploration of massive mailboxes: summary -> period -> thread -> message. Each level is token-cheap.
- **Contact identity linking** -- Cross-platform person mapping (same person across Gmail, WhatsApp, O365). LLM-managed: ts4k stores/queries, the consuming LLM drives linking decisions.
- **Compact output formats** -- Pipe-delimited for listings (~60% savings over JSON), mini XML for message bodies, mixed formats with clear boundaries.
- **Three modes** -- CLI (primary agent interface, zero context cost until used), MCP server (always-loaded for dedicated comms agents), Skill (thin self-documenting stub).
- **Watermark state management** -- Per-platform last-seen tracking. Using `whatsnew` IS the side effect -- watermarks advance on use, no separate save step.
- **Efficiency stats tracking** -- Bytes in/out, tokens saved, per-platform/contact/day breakdowns. Queryable via `status`.
- **Filter configuration** -- Skip lists, category filters, sender allowlists. Multi-layer noise filtering eliminates 80-85% of messages before any LLM processing.

---

## Intended Capabilities

Designed but not yet built:

- **Send/draft with safety rails** -- Bidirectional pipe. Draft-only mode, confirmation flags, per-adapter config. The consuming agent decides; ts4k enforces safety.
- **Docker deployment** -- Bundled container with ts4k + all connectors, process supervision for long-running adapters. Single `docker compose up` for users.
- **Calendar adapters** -- Google Calendar, O365 Calendar. Unified `ts4k cal` command with today/week/next/range/event subcommands.
- **Attendee context enrichment** -- Cross-reference calendar attendees with contacts map + message history. "You have 3 unread from Sarah, last thread: Q1 budget review."
- **Additional adapters** -- Slack, Teams Chat, Telegram, Discord, GitHub Notifications. The adapter interface is designed for 5-8+ sources including non-messaging.
- **Background/resumable batch operations** -- Process large mailboxes in chunks, resume on failure. Status/progress reporting for long-running operations.

---

## Day-One Platforms

Gmail, WhatsApp, O365.

**Design for:** 5-8+ adapters including non-messaging (calendar, Jira, GitHub).

---

## Success Headlines

1. **"Agent morning briefing checks 3 platforms in one call, costs 800 tokens instead of 15,000"** -- daily per-interaction efficiency
2. **"Adding a new communication platform takes an afternoon, not a week"** -- adapter extensibility
3. **"Peter's agents carry one MCP instead of five, freeing context for actual reasoning"** -- context budget savings
4. **"ts4k runs unattended on the NUC, agents just call it and it works"** -- operational reliability
5. **"Data-mining 10 years of communication history across 3 platforms for $100 instead of $10,000"** -- bulk historical analysis at viable cost
6. **"100k people use ts4k to start their day"** -- open source adoption

---

## Key Design Rules

1. **Metadata first, content on demand.** Default to minimum useful response. Agent opts into `--full`, `--tail N`, etc.
2. **No LLM calls inside ts4k.** This is the data layer, not the intelligence layer.
3. **Platform failures are isolated.** If one adapter is down, others still return results. Partial results are better than no results.
4. **Native platform IDs** prefixed with source (`g:`, `w:`, `t:`). No synthetic sequential IDs.
5. **Using a command IS the side effect.** Watermarks update on `whatsnew`. No separate save step.
6. **Format is a feature.** Pipe-delimited for listings (~60% savings over JSON), mini XML for bodies.
7. **Adapter-agnostic output.** Downstream consumers never see platform-specific data. A WhatsApp message and a Gmail message look identical after normalization.

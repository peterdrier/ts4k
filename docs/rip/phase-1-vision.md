# RIP Phase 1: Vision & Purpose

**Project:** ts4k (Token Saver 4000)
**Date:** 2026-02-21
**Participants:** Test User, Claude (facilitator)

---

## Elevator Pitch

> **For** Peter (and eventually other power users running LLM agents)
> **Who** want to integrate multiple communication channels (Gmail, O365, WhatsApp, Telegram, Discord, Slack...) into their LLM workflows but find that raw platform APIs/MCPs burn tokens at unsustainable rates and bloat agent context with per-service tool definitions
> **The** ts4k
> **Is a** normalizing funnel between LLM agents and communication platforms
> **That** reduces token consumption by 60-95% per interaction, unifies N services behind one compact interface, and shrinks the MCP/skill footprint agents need to carry
> **Unlike** loading individual MCPs per platform (which costs 5k+ tokens in context overhead alone, and 10x more per message read)
> **Our approach** wraps the best existing tool per platform (not reinventing APIs), normalizes commands and output formats for LLM consumption, and treats token efficiency as a first-class design constraint

**Primary user:** Peter, via LLM agents. Public GitHub from day one.

**Two usage patterns:**
- **Real-time** — daily briefings, interactive agent queries, quick check-ins
- **Bulk historical** — data-mining years of communication history at viable cost (target: 10 years for ~$100 instead of ~$10,000)

**Bidirectional:** Read-primary, but send/draft are in scope with safety rails (draft-only modes, confirmation flags).

---

## Success Headlines (6-month horizon)

1. **"Agent morning briefing checks 3 platforms in one call, costs 800 tokens instead of 15,000"** — daily per-interaction efficiency
2. **"Adding a new communication platform takes an afternoon, not a week"** — adapter extensibility
3. **"Peter's agents carry one MCP instead of five, freeing context for actual reasoning"** — context budget savings
4. **"ts4k runs unattended on the NUC, agents just call it and it works"** — operational reliability
5. **"Data-mining 10 years of communication history across 3 platforms for $100 instead of $10,000"** — bulk historical analysis at viable cost
6. **"100k people use ts4k to start their day"** — open source adoption and community traction

---

## Anti-Goals

| # | Statement | Notes |
|---|-----------|-------|
| 1 | **ts4k is not an AI.** No analysis, no summarization, no decision-making about content. | It's the data layer, not the intelligence layer. |
| 2 | **ts4k IS bidirectional.** Send/reply flow through ts4k, with safety guardrails (draft-only modes, confirmation flags). | It's a pipe — pipes go both ways. But carefully. |
| 3 | **ts4k is not a connector.** It wraps existing MCPs/tools. It doesn't implement OAuth, IMAP, or platform APIs. | The various tools already handle their own auth. ts4k may provide setup docs to make it easier for people. |
| 4 | **ts4k is not a real-time listener.** No websockets, no push notifications. | But cheap polling at short intervals (e.g., 5 min) could be viable if token costs are low enough. |
| 5 | **ts4k is not a message database.** It fetches, normalizes, and passes through. | But it DOES maintain contact identity links and watermark state. May cache for long-running operations (backloads, voice note translation). |
| 6 | **No UI.** CLI + MCP are the interfaces. | Dual help: `ts4k help` for humans, `ts4k llm` for LLM-optimized help (could be a built-in skill). |
| 7 | **ts4k doesn't own the intelligence.** It may enforce safety (draft-only, send confirmation), but what to send, who to contact, how to respond — that's the consuming agent. | Decisions belong to the LLM, not the pipe. |

---

## Success Criteria

| Criterion | Measure | Timeline | Priority |
|-----------|---------|----------|----------|
| Token reduction on message reads | 60-95% reduction vs raw MCP output, verified on real data | Phase 1 | Must-have |
| Unified whatsnew across 3+ platforms | Single call returns merged, normalized feed | Phase 1-2 | Must-have |
| Context footprint | One MCP (5-8 tools) replaces 3-5 platform MCPs | Phase 1 | Must-have |
| Adapter addition effort | New adapter working in < 1 day of dev time | Ongoing | Must-have |
| Bidirectional send/draft | Safe passthrough with draft-only mode option | Phase 2 | Must-have |
| Bulk historical viability | 10 years of history normalized for < $100 LLM cost | Phase 2-3 | Must-have |
| Contact identity linking | Cross-platform identity map, LLM-managed | Phase 2 | Must-have |
| Operational reliability | Unattended on NUC, isolated adapter failures, status reporting | Phase 2 | Must-have |
| Efficiency stats | Per-session and aggregate: messages processed, bytes in vs out, tokens saved, per-platform/contact/day breakdowns | Phase 1-2 | Nice-to-have (great for adoption story) |
| Installable by others | pip install + docs, someone else can get it running | Phase 2-3 | Nice-to-have |
| Open source adoption | 100k users | Long-term | Aspirational |

---

## Key Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Token savings per-message AND context overhead savings are both first-class goals | Same value prop (cost reduction) expressed differently |
| 2 | Day-one adapters: Gmail, WhatsApp, O365. Design for 5-8 adapters. | Start with Peter's actual needs, architect for growth |
| 3 | Wraps existing MCPs/connectors — doesn't build its own | Existing tools handle auth/API. ts4k focuses on normalization and efficiency. |
| 4 | Bidirectional — send/reply in scope with safety rails | It's a pipe, pipes go both ways. Draft-only modes, confirmation flags. |
| 5 | Contact identity linking in scope, LLM-managed | ts4k stores/queries the map, the consuming LLM drives linking decisions |
| 6 | Long-running operations in scope | Backloading history, voice note translation queues. Needs status/progress reporting. |
| 7 | Dual help system — human-readable and LLM-optimized | `ts4k help` vs `ts4k llm`. LLM help could ship as built-in skill. |
| 8 | Stats tracking in scope | Fun, useful for tuning, and compelling for adoption ("1.57kb became 237 bytes") |
| 9 | Command surface derived bottom-up from provider capability intersection | MVP = what all 3 providers support. Then layer on ideal-world and batch/macro commands. |

---

## Open Questions (to resolve in later phases)

- What specific MCPs exist for Gmail, WhatsApp, O365 and what do they expose?
- Draft-only mode mechanics — how does safety gating work for sends?
- Where does contact identity state live and what's the schema?
- Polling feasibility — how cheap is a 5-min check cycle?
- Caching strategy for long-running bulk operations
- What does "adapter in < 1 day" actually require? What's the adapter contract?

# RIP: ts4k (Token Saver 4000)

**Generated:** 2026-02-21
**Participants:** Test User (stakeholder), Claude (facilitator)

---

## 1. Vision & Purpose

### Elevator Pitch

> **For** Peter (and eventually other power users running LLM agents)
> **Who** want to integrate multiple communication channels into LLM workflows but find that raw platform APIs/MCPs burn tokens at unsustainable rates and bloat agent context with per-service tool definitions
> **The** ts4k
> **Is a** normalizing funnel between LLM agents and communication platforms
> **That** reduces token consumption by 60-95% per interaction, unifies N services behind one compact interface, and shrinks the MCP/skill footprint agents need to carry
> **Unlike** loading individual MCPs per platform (which costs 5k+ tokens in context overhead alone, and 10x more per message read)
> **Our approach** wraps the best existing tool per platform (not reinventing APIs), normalizes commands and output formats for LLM consumption, and treats token efficiency as a first-class design constraint

**Primary user:** Peter, via LLM agents. Public GitHub from day one.

**Two usage patterns:** Real-time (daily briefings, interactive queries) and bulk historical (data-mining years of history at viable cost).

**Bidirectional:** Read-primary, but send/draft in scope with safety rails.

### Success Headlines (6-month horizon)

1. **"Agent morning briefing checks 3 platforms in one call, costs 800 tokens instead of 15,000"**
2. **"Adding a new communication platform takes an afternoon, not a week"**
3. **"Peter's agents carry one MCP instead of five, freeing context for actual reasoning"**
4. **"ts4k runs unattended on the NUC, agents just call it and it works"**
5. **"Data-mining 10 years of communication history across 3 platforms for $100 instead of $10,000"**
6. **"100k people use ts4k to start their day"**

### Anti-Goals

| # | Statement | Notes |
|---|-----------|-------|
| 1 | ts4k is not an AI — no analysis, no summarization | Data layer, not intelligence layer |
| 2 | ts4k IS bidirectional — send/reply with safety guardrails | Draft-only modes, confirmation flags |
| 3 | ts4k is not a connector — wraps existing MCPs/tools | Upstream handles auth/API |
| 4 | ts4k is not a real-time listener — no websockets, no push | Cheap polling at short intervals could be viable |
| 5 | Not a message database — but maintains contact links, watermarks, and explicit preload cache | Caching is explicit and user-initiated, not automatic |
| 6 | No UI — CLI + MCP + Skill are the interfaces | Dual help: human-readable + LLM-optimized |
| 7 | ts4k doesn't own the intelligence | Safety enforcement yes, decision-making no |

### Success Criteria

| Criterion | Measure | Timeline | Priority |
|-----------|---------|----------|----------|
| Token reduction on reads | 60-95% vs raw MCP output | Phase 1 | Must-have |
| Unified whatsnew | Single call, merged normalized feed | Phase 1-2 | Must-have |
| Context footprint | One MCP replaces 3-5 | Phase 1 | Must-have |
| Adapter addition effort | New adapter in < 1 day | Ongoing | Must-have |
| Bidirectional send/draft | Safe passthrough, draft-only option | Phase 5 | Must-have |
| Bulk historical viability | 10 years for < $100 LLM cost | Phase 4 | Must-have |
| Contact identity linking | Cross-platform, LLM-managed | Phase 2 | Must-have |
| Operational reliability | Unattended on NUC, isolated failures | Phase 2 | Must-have |
| Efficiency stats | Bytes in/out, tokens saved, breakdowns | Phase 1-2 | Nice-to-have |
| Installable by others | pip install + docs | Phase 5 | Nice-to-have |
| Open source adoption | 100k users | Long-term | Aspirational (but a design goal) |

---

## 2. Stakeholders & Context

### Stakeholder Map

| Stakeholder | Interest | Influence | Key Needs |
|-------------|----------|-----------|-----------|
| Peter (dev + user) | Build it, use it daily | High | Clean architecture, reliable, fun |
| LLM agents | Compact, predictable interface | High | Minimal tokens, consistent format |
| Future OSS users | Easy install, good docs | Low → High | pip install + docs, not Peter-specific |
| Upstream connector maintainers | Unaware of ts4k | Medium | If they break API, ts4k breaks |

### Current State

**Existing connectors (working):**
- **Gmail:** user/google_workspace_mcp (Python FastMCP fork, HTTP on :51429, read-only)
- **WhatsApp:** whatsapp-mcp (Go bridge + Python FastMCP, stdio, read-only, send removed)
- **Google Calendar:** @cocal/google-calendar-mcp (Node.js, read-only)
- **O365:** Nothing yet

**Trigger:** Two nights ago, Peter loaded Gmail + WhatsApp + Discord MCPs into an agent and saw tokens burning at unsustainable rates.

### Constraints

**Hard:** Ubuntu NUC deployment, CLI cross-platform (Win/Mac/Linux), wraps existing connectors, latest LTS versions, designed for 100k self-hosted installs.

**Soft:** Side project (no deadline), fork upstream if needed, public GitHub from day one.

### Dependencies

| Dependency | Risk |
|------------|------|
| user/google_workspace_mcp (fork) | Low — Peter controls |
| whatsapp-mcp Go bridge | Low — local |
| WhatsApp Web protocol (whatsmeow) | **Medium** — Meta could break |
| O365 connector (TBD) | **Research needed** |
| @cocal/google-calendar-mcp | Low — already working |
| O365 calendar (via Softeria) | Low — same server handles mail + calendar |
| MCP protocol/SDK | Low — Anthropic-backed |

---

## 3. Scope & Requirements

### Command Surface

| Command | Alias | What It Does | Priority |
|---------|-------|-------------|----------|
| `whatsnew` | `wn` | Unified new activity feed across all platforms | Must-have |
| `get` | `g` | Read single message, normalized | Must-have |
| `thread` | `t` | Read conversation, normalized | Must-have |
| `list` | `l` | Search/filter messages | Must-have |
| `overview` | `o` | Hierarchical summary with drill-down | Must-have |
| `contacts` | `c` | Cross-platform identity map | Must-have |
| `send` | `s` | Send via platform (safety-railed) | Should-have |
| `draft` | `d` | Create draft via platform | Should-have |
| `cal` | — | Unified calendar view (today/week/next) | Should-have |
| `status` | `st` | Health, watermarks, stats, jobs | Should-have |
| `help` | `h` | Human-readable help | Must-have |
| `skill` | — | LLM-optimized command reference | Must-have |

### Three Operational Modes

| Mode | Context Cost | When To Use |
|------|-------------|-------------|
| **MCP** | ~1-2k tokens permanently | Dedicated comms agent |
| **CLI** | 0 until used | Scripts, cron, occasional use |
| **Skill** | 0 until invoked | General-purpose agents (sweet spot) |

`ts4k skill` outputs self-documenting LLM help. The Claude skill `/ts` is a thin stub that calls it. Docs live in ts4k, never drift.

### Output Formats

Configurable via `--format pipe|json|xml` (`-f p|j|x`):
- **Pipe-delimited** — default, most compact for LLM listings
- **JSON** — best for fact extraction pipelines (Haiku → Sonnet/Opus), programmatic use
- **XML** — fastest LLM parsing

### Scope Boundary

**In:** Normalize pipeline, 11+ commands, 3 modes, 3 day-one adapters (Gmail/WhatsApp/O365), calendar adapters (Google Calendar/O365), adapter interface for 5-8+ sources (including non-messaging), pipe/JSON/XML output, watermarks, contacts, stats, safety-railed send/draft, background tasks, Docker template.

**Out:** Building connectors (wraps existing), OAuth/auth, real-time push, UI, automatic contact resolution, LLM calls inside ts4k, calendar write operations (create/accept/decline — deferred).

---

## 4. Architecture & Technical Options

### Key Decisions

**ADR-1: Python** (latest LTS, 3.12). Two of three upstream connectors are Python. Best HTML processing ecosystem. Official MCP SDK.

**ADR-2: MCP Client** for upstream integration. Clean protocol boundary, hot-swappable, language-agnostic. Direct import as optional optimization.

**ADR-3: JSON files** for all state in `~/.config/ts4k/`. Single user, no concurrency concern. Human/LLM readable.

**ADR-4: Watermarks scoped per-context.** CLI defaults to project folder (`.ts4k/watermarks.json`). MCP mode uses caller-provided session ID. Multiple agents don't steal each other's messages.

### System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        ts4k                                  │
│                                                              │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐               │
│  │    CLI    │  │ MCP Server│  │   Skill   │               │
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
│  │  Normalize → Filter → Format (p/j/x)     │               │
│  └──────────────────┬───────────────────────┘               │
│                     ▼                                        │
│  ┌──────────────────────────────────────────┐               │
│  │      Adapter Layer (MCP Client)           │               │
│  │  Gmail | WhatsApp | O365 | Future...      │               │
│  └──────────────────┬───────────────────────┘               │
│                     ▼                                        │
│  ┌──────────────────────────────────────────┐               │
│  │    State (JSON, per-context watermarks)    │               │
│  └──────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────┘
         │              │              │
         ▼              ▼              ▼
   google_workspace  whatsapp-mcp   o365-mcp
       _mcp          (Go+Py)        (TBD)
```

### Docker Deployment Option

ts4k + upstream MCPs in a single container, only ts4k's MCP endpoint exposed. Credential isolation — consuming agent can't reach upstream MCPs or tokens. Provided as Dockerfile + docker-compose template, not pre-built image (users build locally with their own credentials).

---

## 5. Prototyping Strategy

### Prototype Sequence

```
Day 1 morning:   [P1: Normalizer] + [P2: MCP Bridge]     (parallel)
Day 1 afternoon: [P3: Full Loop]                           (depends on P1+P2)
Day 2 morning:   [P4: Bulk Probe]                          (depends on P2)
Day 2 afternoon: [P5: Container]                           (independent)
```

**P1 — The Normalizer:** Test HTML → normalized on 20-30 real emails. Target: 70%+ reduction.
**P2 — The MCP Bridge:** Connect to google_workspace_mcp + whatsapp-mcp as MCP client. Parallel calls.
**P3 — The Full Loop:** Wire P1+P2 into `ts4k wn --source gmail`. All three output formats.
**P4 — The Bulk Probe:** Paginate years of history for one contact. Test background task pattern.
**P5 — The Container:** Docker bundle, single endpoint, credential isolation.

### Go/No-Go

- **Green:** 70%+ token reduction, MCP client reliable, end-to-end works → full build
- **Yellow:** 50-70% reduction or slow overview → adjust scope
- **Red:** < 50% reduction → value prop doesn't hold, stop and rethink

---

## 6. Implementation Plan

### Delivery Phases

| Phase | Name | Days | Key Deliverables |
|-------|------|------|-----------------|
| 0 | Prototype Validation | 1-2 | Normalizer, MCP bridge, full loop, bulk probe, container |
| 1 | Gmail MVP | 3-5 | Core pipeline, Gmail adapter, CLI (wn/g/t/l/h/skill), watermarks, output formats |
| 2 | WhatsApp + Contacts | 6-8 | WhatsApp adapter, parallel calls, contacts, status, stats, filters |
| 3 | O365 + MCP Server | 9-12 | O365 adapter, MCP server mode, 3-platform whatsnew |
| 4 | Overview + Bulk | 13-16 | Overview drill-down, background tasks, cache, resumable batch |
| 5 | Send + Docker + Release | 17-22 | Send/draft, Docker template, PyPI, docs, public release |
| 6 | Calendar | 23-26 | Calendar adapters (Google Calendar, O365), unified schedule view, attendee context enrichment |

Day estimates are upper bounds. Peter expects faster.

### Milestones

| # | Milestone | Target | Unlocks |
|---|-----------|--------|---------|
| M0 | Go/No-Go | Day 2 | Full implementation |
| M1 | First useful day (Peter uses `ts4k wn` on real Gmail) | Day 5 | Daily use |
| M2 | Multi-platform feed (Gmail + WhatsApp) | Day 8 | O365 |
| M3 | Three platforms + MCP server | Day 12 | Bulk historical |
| M4 | Bulk historical viable | Day 16 | Release |
| M5 | Public release | Day 22 | Community |
| M6 | Calendar context ("what's my day + who am I meeting") | Day 26 | Cohesive person view |

### Top Risks

| Risk | L | I | Mitigation |
|------|---|---|------------|
| Normalization < 70% savings | Low | High | Phase 0 validates on real data |
| WhatsApp protocol breaks | Med | High | Fork-friendly, community patches |
| O365 connector quality | Med | Med | 3 options evaluated, can fork |
| Opportunity cost (time vs other priorities) | High | Med | Awareness. Pause/resume. |

### Immediate Next Steps

1. **Phase 0: P1 + P2 in parallel.** Normalizer + MCP bridge on real Gmail data.
2. **Evaluate O365 MCPs.** Install Softeria, test data quality.
3. **Set up repo.** `pyproject.toml`, `src/ts4k/`, prototype code grows into real code.

---

## Appendix: Key Decisions Log

| # | Decision | Phase | Rationale |
|---|----------|-------|-----------|
| 1 | Token savings per-message AND context overhead are both first-class goals | 1 | Same value prop (cost reduction) expressed differently |
| 2 | Day-one adapters: Gmail, WhatsApp, O365. Design for 5-8. | 1 | Start with Peter's needs, architect for growth |
| 3 | Wraps existing MCPs, doesn't build connectors | 1 | Existing tools handle auth/API |
| 4 | Bidirectional — send/reply with safety rails | 1 | Pipe goes both ways. Draft-only modes. |
| 5 | Contact identity linking, LLM-managed | 1 | ts4k stores/queries, LLM drives linking |
| 6 | Long-running operations in scope | 1 | Backloads, voice note translation queues |
| 7 | Dual help: `help` for humans, `skill` for LLMs | 1 | Usability + context savings |
| 8 | Stats tracking in scope | 1 | Fun, tuning, and adoption story |
| 9 | Command surface derived from provider intersection | 1 | Bottom-up from what adapters actually support |
| 10 | Fork-friendly stance on upstream connectors | 2 | Don't wait for others |
| 11 | NUC is deployment target (Ubuntu, always-on) | 2 | WhatsApp bridge needs 24/7 |
| 12 | No hard deadline. Buildable in days once planned. | 2 | Side project, fast execution expected |
| 13 | ts4k is a "what's changed" funnel, not just messaging | 2 | Calendar, Jira, GitHub all viable later |
| 14 | 100k users is a design goal (self-hosted installs) | 2 | Easy install, good docs, reliable defaults |
| 15 | O365 is day 2, surface area research done | 2 | Softeria recommended, elyxlz as Python backup |
| 16 | Bulk historical is a batching problem | 2 | WhatsApp: local SQLite. Gmail/O365: paginated fetch. |
| 17 | Single-letter command aliases | 3 | Token savings + convenience |
| 18 | Send/draft in scope with safety rails | 3 | Draft-only mode, confirmation flags |
| 19 | `overview` for hierarchical drill-down | 3 | Summary → period → thread → message. Cheap at each level. |
| 20 | Three operational modes: MCP, CLI, Skill | 3 | Agent picks based on context budget |
| 21 | `ts4k skill` is self-documenting | 3 | Docs live in ts4k, Claude skill is thin pointer |
| 22 | Python (latest LTS) | 4 | 2/3 connectors Python, best HTML processing, official MCP SDK |
| 23 | MCP Client for upstream integration | 4 | Clean boundary, hot-swappable, language-agnostic |
| 24 | JSON for all state | 4 | Single user, human/LLM readable, simple |
| 25 | Docker container deployment option | 5 | Credential isolation, simplified deployment, security for untrusted agents |
| 26 | Output format configurable: pipe/json/xml | 5 | Different consumers, different needs |
| 27 | Background tasks for long-running operations | 5 | Preload history into cache for later interactive query |
| 28 | Watermarks scoped per-context | 6 | Multiple agents/sessions don't steal each other's messages |
| 29 | Docker template, not pre-built image | 6 | Users build locally with their own credentials |
| 30 | Biggest risk: opportunity cost | 6 | Awareness + ability to pause/resume |
| 31 | Calendar access — cohesive person view, not token savings | 6 | Calendar data is already compact; value is cross-referencing attendees with message history |
| 32 | Calendar is read-only initially | 6 | Create/accept/decline deferred; read-only matches messaging Phase 1-4 pattern |

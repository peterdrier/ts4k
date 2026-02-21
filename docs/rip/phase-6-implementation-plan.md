# RIP Phase 6: Implementation Plan

**Project:** ts4k (Token Saver 4000)
**Date:** 2026-02-21
**Participants:** Test User, Claude (facilitator)

---

## Team Structure

Solo dev: Peter + Claude Code. No team org needed. Side project, evenings/weekends, intensity varies.

---

## Delivery Phases

### Phase 0: Prototype Validation (Day 1-2)

Defined in Phase 5. The real starting point.

- **Deliverables:** P1 (Normalizer), P2 (MCP Bridge), P3 (Full Loop), P4 (Bulk Probe), P5 (Container)
- **Capabilities validated:** Token reduction on real data, MCP client connectivity, end-to-end flow, bulk feasibility, Docker isolation
- **Exit criteria:** Go/No-Go decision. Green = 70%+ token reduction, reliable MCP client, working end-to-end Gmail flow.
- **Key risk:** P1 shows normalization doesn't deliver. (Unlikely but project-ending.)

Phase 0 output becomes Phase 1 code.

### Phase 1: Gmail MVP (Days 3-5)

Fetch one platform's new messages and make them small. First useful day.

- **Deliverables:**
  - Core normalize pipeline (HTML strip, reply chain dedup, whitespace collapse, signature removal)
  - Core format pipeline: `-f pipe|json|xml` (`-f p|j|x`)
  - Gmail adapter (MCP client to google_workspace_mcp)
  - CLI entry point: `wn`, `g`, `t`, `l`, `h`, `skill`
  - Single-letter aliases
  - Watermark state — **scoped per-context** (defaults to current project folder, e.g., `.ts4k/watermarks.json`)
  - `ts4k skill` — LLM-optimized command reference
  - `ts4k help` — human-readable help
- **Capabilities:** Normalize pipeline, whatsnew, get, thread, list, watermarks, output formats, CLI mode, dual help, Gmail adapter
- **Exit criteria:** `ts4k wn` returns normalized Gmail feed. `ts4k g <id>` returns clean message. Token savings verified. Peter is using it.
- **Key risk:** Normalization edge cases (calendar invites, multipart MIME, non-English)

### Phase 2: WhatsApp + Contacts + Stats (Days 6-8)

Second adapter, cross-platform identity, efficiency tracking.

- **Deliverables:**
  - WhatsApp adapter (MCP client to whatsapp-mcp)
  - Parallel adapter calls (asyncio — `wn` hits Gmail + WhatsApp simultaneously)
  - Contact identity map (JSON, LLM-managed): `ts4k c` — create/link/query
  - `ts4k st` — status (watermarks, adapter health, running jobs)
  - Efficiency stats (bytes in/out, messages processed, tokens saved, per-platform)
  - Filter config (skip lists, sender allowlists, per-adapter)
- **Capabilities:** WhatsApp adapter, contacts, status, stats, filters
- **Exit criteria:** `ts4k wn` returns unified Gmail + WhatsApp feed. Contacts linkable cross-platform. Stats show real savings.
- **Key risk:** WhatsApp data shapes differ from Gmail. Normalization needs per-adapter quirk handling.

### Phase 3: O365 + MCP Server Mode (Days 9-12)

Third adapter, second integration mode.

- **Deliverables:**
  - Evaluate and integrate O365 MCP (Softeria, elyxlz fork, or other)
  - O365 adapter
  - MCP server mode (ts4k as MCP server, same commands as tools)
  - MCP mode watermark scoping (caller passes context/session ID so multiple agents don't steal each other's messages)
  - Three-platform parallel whatsnew
  - Adapter interface stabilized and documented
- **Capabilities:** O365 adapter, MCP server mode
- **Exit criteria:** `ts4k wn` returns 3-platform unified feed. Works as MCP server in Claude Code. Adapter interface documented.
- **Key risk:** O365 connector quality. May need fork.

### Phase 4: Overview + Bulk Historical (Days 13-16)

The "$100 for 10 years of history" use case.

- **Deliverables:**
  - `ts4k o` — overview with hierarchical drill-down (`--period`, `--contact`)
  - Background task system (start, check status, query cached results)
  - Local cache for preloaded data
  - Resumable batch processing (checkpoint, resume from last position)
  - `ts4k status` extended with running jobs and progress
- **Capabilities:** Overview, batch/resumable backload, background tasks
- **Exit criteria:** Aggregate metadata for real contact with years of history. Background preload works. Cached results queryable.
- **Key risk:** Performance on large mailboxes. Cache growth.

### Phase 5: Send/Draft + Docker + Public Release (Days 17-22)

Bidirectional, containerized, installable.

- **Deliverables:**
  - `ts4k s` / `ts4k d` — send/draft with safety rails
  - Draft-only mode (per-adapter config), confirmation flags
  - Dockerfile + docker-compose.yml **template** (users build locally with their own config/tokens — no pre-built image)
  - `pyproject.toml` for PyPI
  - `pipx install ts4k` / `uv tool install ts4k` working
  - README, setup guide, adapter docs
  - Public release: GitHub + PyPI
- **Capabilities:** Send/draft, Docker template, packaging, docs
- **Exit criteria:** Non-Peter person installs ts4k, configures Gmail, runs `ts4k wn`. Docker compose template works. Send/draft with safety rails.
- **Key risk:** Docs quality for 100k-user goal. Docker + WhatsApp headless auth.

---

## Timeline Overview

```
       Day 1-2        Day 3-5      Day 6-8     Day 9-12    Day 13-16   Day 17-22
    ┌────────────┐ ┌───────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
    │  Phase 0   │ │ Phase 1   │ │ Phase 2  │ │ Phase 3 │ │ Phase 4 │ │ Phase 5 │
    │ Prototypes │→│ Gmail MVP │→│ WhatsApp │→│ O365 +  │→│Overview │→│ Send +  │
    │ Go/No-Go   │ │ CLI+Norm  │ │ Contacts │ │ MCP Srv │ │ Bulk    │ │ Docker  │
    │            │ │ Formats   │ │ Stats    │ │         │ │ Backgrnd│ │ Release │
    └────────────┘ └───────────┘ └──────────┘ └─────────┘ └─────────┘ └─────────┘
```

Peter expects this will go faster than 22 days. Day estimates are upper bounds.

---

## Milestones

| # | Milestone | Target | Verification | Unlocks |
|---|-----------|--------|-------------|---------|
| M0 | Go/No-Go | Day 2 | Token reduction ≥70%, MCP client works | Full implementation |
| M1 | First useful day | Day 5 | Peter uses `ts4k wn` on real Gmail | WhatsApp, daily use |
| M2 | Multi-platform feed | Day 8 | `ts4k wn` returns Gmail + WhatsApp unified | O365, broader use |
| M3 | Three platforms + MCP server | Day 12 | ts4k as MCP server with 3 adapters | Bulk historical |
| M4 | Bulk historical viable | Day 16 | Background preload + interactive query on real history | Send/draft, release |
| M5 | Public release | Day 22 | pip-installable, documented, non-Peter runs it | Community, 100k |

---

## Risk Register

| Risk | L | I | Mitigation | Contingency |
|------|---|---|------------|-------------|
| Normalization < 70% savings | Low | High | Test on real data in Phase 0 | Pivot to metadata-only mode |
| WhatsApp protocol breaks (Meta) | Med | High | Fork-friendly, community patches fast | Adapter fails gracefully, others continue |
| O365 connector poor quality | Med | Med | 3 options evaluated, can fork | Defer O365, proceed with 2 platforms |
| MCP client reliability | Low | High | Phase 0 validates. Retry logic. | Fall back to direct import for Python MCPs |
| Bulk overview too slow | Med | Med | Background tasks, caching, incremental | Limit interactive to recent, background for full |
| Docker + WhatsApp headless auth | Med | Low | Research whatsmeow headless | Docker without WhatsApp initially |
| Watermark conflicts between agents | Med | Med | Per-context watermark scoping from Phase 1 | Already mitigated by design |
| **Opportunity cost — time here vs other priorities** | **High** | **Med** | **Awareness. RIP is done, execute fast.** | **Pause and resume. ts4k isn't going anywhere.** |

---

## Resource Summary

| Resource | Cost | Notes |
|----------|------|-------|
| Peter's time | Side project, intensity varies | Biggest constraint |
| Claude Code | Per-usage API cost | Primary dev tool |
| NUC (Ubuntu) | Already running | Deployment target |
| Upstream MCPs | Free/open source | Gmail fork, WhatsApp local, O365 TBD |
| PyPI | Free | Public release |

---

## Immediate Next Steps

1. **Phase 0, P1 + P2 in parallel.** Normalizer + MCP bridge. Real Gmail data. Validate the value prop.
2. **Evaluate O365 MCP options.** Install Softeria, see what it returns. Try elyxlz if Python-native is preferred.
3. **Set up repo.** `pyproject.toml`, `src/ts4k/`, basic structure. Prototype code grows into real code.

---

## Decisions Made in Phase 6

| # | Decision | Rationale |
|---|----------|-----------|
| 28 | Watermark state scoped per-context (project folder in CLI, session ID in MCP) | Multiple agents/sessions can't steal each other's messages. Assistant agent and interactive session each have own watermarks. |
| 29 | No pre-built Docker image. Provide Dockerfile + docker-compose template for local build. | Container needs user-specific credentials/tokens. Users build locally. |
| 30 | Biggest risk is opportunity cost (time on ts4k vs other priorities) | Not a technical risk. Awareness + ability to pause/resume is the mitigation. |

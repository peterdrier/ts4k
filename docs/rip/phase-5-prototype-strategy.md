# RIP Phase 5: Prototyping Strategy

**Project:** ts4k (Token Saver 4000)
**Date:** 2026-02-21
**Participants:** Test User, Claude (facilitator)

---

## Critical Assumptions (Priority Order)

| # | Assumption | Confidence | Impact if Wrong | Cost to Validate |
|---|-----------|-----------|----------------|-----------------|
| 1 | HTML normalization can reliably achieve 60-95% token reduction on real email | Likely | Project-ending — this IS the value prop | Hours |
| 2 | MCP client can connect to and call upstream MCPs reliably | Likely | Major rework — adapter architecture depends on this | Hours |
| 3 | Compact output formats (pipe/JSON/XML) are meaningfully more token-efficient than raw MCP responses | Likely | Minor — switch format | Hours |
| 4 | `overview` can aggregate metadata for large mailboxes in reasonable time (with background task option) | Uncertain | Major — bulk historical use case dies | Hours-Days |
| 5 | Three upstream MCPs can be called in parallel via asyncio | Likely | Minor — fall back to sequential | Hours |
| 6 | Docker container can bundle ts4k + upstream MCPs with credential isolation | Uncertain | Minor — Docker is optional deployment mode | Days |
| 7 | `ts4k skill` self-documentation approach works in practice | Uncertain | Minor — fall back to static skill file | Hours |

---

## Prototype Plan

### Prototype 1: "The Normalizer"

- **Testing:** Can we reliably strip HTML email to 5-20% of original token count while preserving informational content?
- **Build:** Standalone Python script. Takes raw Gmail message content (fetched manually from google_workspace_mcp), runs through beautifulsoup4/html2text pipeline. Measures before/after token counts. Test against 20-30 real emails: newsletters, conversations, automated notifications, rich HTML, plain text.
- **Fidelity:** Working code, throwaway script
- **Success:** Average 70%+ reduction. Worst case (plain text) still 20%+ from metadata/header stripping. No information loss in actual content.
- **Failure:** Less than 50% average reduction, or frequent content corruption (lost links, garbled text).
- **Time box:** 2-3 hours

### Prototype 2: "The MCP Bridge"

- **Testing:** Can ts4k act as an MCP client, connect to google_workspace_mcp, call search + read, and get structured data back?
- **Build:** Python script using MCP SDK client. Connect to already-running google_workspace_mcp on :51429. Call `search_gmail_messages`, then `get_gmail_message_content`. Then same against whatsapp-mcp. Then try both in parallel with asyncio.
- **Fidelity:** Working code, becomes the real adapter skeleton
- **Success:** Reliable connection, structured data back, parallel calls work.
- **Failure:** MCP client SDK issues with HTTP transport, or upstream MCPs return unexpected formats.
- **Time box:** 2-3 hours

### Prototype 3: "The Full Loop"

- **Testing:** Can we go from `ts4k wn` to formatted, normalized output for Gmail?
- **Build:** Wire Prototype 1 + 2 into a minimal CLI. `ts4k wn --source gmail` fetches new messages via MCP client, normalizes, formats output. Watermark tracking. This is basically the Phase 1 MVP.
- **Output format options:** Test all three formats on the same data:
  - Pipe-delimited (`-f pipe`) — default, most compact for LLM listings
  - JSON (`-f json`) — best for fact extraction pipelines (Haiku → Sonnet/Opus), programmatic use
  - XML (`-f xml`) — fastest LLM parsing per claude.ai analysis
  - Flag: `--format pipe|json|xml` or `-f p|j|x`. Default configurable.
- **Fidelity:** Working code, becomes the real thing
- **Success:** One command, formatted output in all three formats, measurable token savings vs calling Gmail MCP directly.
- **Failure:** End-to-end latency > 10s, or output quality is poor.
- **Time box:** Half a day

### Prototype 4: "The Bulk Probe"

- **Testing:** Can `overview` aggregate metadata for a real contact with years of history? What about background preloading?
- **Build:** Using MCP bridge from Prototype 2, paginate through all messages for one contact. Count messages per month, identify threads, calculate sizes. Measure time and API calls. Test background task pattern: kick off aggregation, check status, query results when ready.
- **Background task pattern:**
  - `ts4k o alice@acme.com --background` — starts aggregation, returns job ID
  - `ts4k status` — shows running jobs and progress
  - Results cached locally for interactive querying later
  - "Go load Peter's history into cache so I can query it interactively in a few hours"
- **Fidelity:** Working code, informs overview and background task design
- **Success:** Can aggregate 1000+ messages in < 30 seconds for interactive use. Background mode works for larger datasets. Cached results queryable.
- **Failure:** Pagination unreliable, or aggregation takes minutes per contact with no background option.
- **Time box:** 3-4 hours

### Prototype 5: "The Container"

- **Testing:** Can ts4k + google_workspace_mcp + whatsapp-mcp live in a Docker container with only ts4k's MCP endpoint exposed?
- **Build:** Dockerfile + docker-compose. Bundle ts4k and upstream MCPs. Expose ts4k's MCP port only. Test that an external agent can call ts4k but cannot reach upstream MCPs or credentials.
- **Fidelity:** Working Docker setup
- **Success:** Single `docker compose up`, ts4k works, credentials not accessible from outside.
- **Failure:** Upstream MCPs have OS dependencies that complicate containerization, or WhatsApp bridge QR code auth doesn't work headless.
- **Time box:** Half a day

---

## Prototype Sequence

```
Day 1 morning:   [P1: Normalizer] + [P2: MCP Bridge]     (parallel, independent)
Day 1 afternoon: [P3: Full Loop]                           (depends on P1 + P2)
Day 2 morning:   [P4: Bulk Probe]                          (depends on P2)
Day 2 afternoon: [P5: Container]                           (independent, after P3 proves core works)
```

---

## Go/No-Go Criteria

### Green Light → Full Implementation
- P1 shows 70%+ token reduction on real email data
- P2 connects reliably to upstream MCPs
- P3 produces useful end-to-end output
- → Proceed to full implementation as planned

### Yellow Light → Adjust Plan
- P1 shows 50-70% reduction → normalization needs more work, but value prop holds
- P4 shows overview is slow for large mailboxes → background tasks become must-have, interactive overview is limited to recent data
- → Adjust scope and priorities, proceed with modifications

### Red Light → Stop and Rethink
- P1 shows < 50% reduction → the value prop doesn't hold (unlikely given real HTML email bloat, but honest criteria)
- P2 shows MCP client pattern is fundamentally unreliable → rethink adapter architecture
- → Stop, regroup, potentially different approach

---

## Decisions Made in Phase 5

| # | Decision | Rationale |
|---|----------|-----------|
| 25 | Docker container deployment option — ts4k + upstream MCPs bundled, single MCP endpoint exposed | Credential isolation, simplified deployment, security boundary for less-trusted agents (OpenClaw). |
| 26 | Output format is configurable: pipe (default, most compact), JSON (fact extraction, programmatic), XML (fastest LLM parsing). `--format pipe|json|xml` or `-f p|j|x`. | Different consumers have different needs. LLMs want pipe/XML. Programs want JSON. Haiku→Sonnet pipelines want JSON rows. |
| 27 | Long-running operations can be background tasks with status tracking and local caching. | Bulk history aggregation may take minutes-hours. "Load history into cache" is a valid use case. Implies ts4k stores cached normalized data for preloaded operations. |

### Boundary Shift Note

Decision 27 means ts4k now stores message data (normalized, cached) for explicit preload operations. This shifts the earlier anti-goal of "not a message database" — ts4k is still not a database in the primary flow (fetch → normalize → pass through), but it IS a cache for background/preload operations. The distinction: caching is explicit, user-initiated, and for performance. Not automatic, not permanent.

---

## Open Questions (to resolve in Phase 6)

- Background task implementation: in-process async, or separate worker process?
- Cache eviction policy: how long do preloaded results live?
- Cache location: `~/.config/ts4k/cache/` or alongside state files?
- How does `ts4k status` present running background jobs?
- Docker compose structure: how do upstream MCPs share credentials securely within the container?

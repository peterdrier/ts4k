# Token Cost of Message IDs and Timestamps

Research session 2026-02-24. Measured with Anthropic token counting API (claude-sonnet-4).

## Measured Token Costs

| Format | Chars | Tokens |
|--------|------:|-------:|
| Gmail ID (`g:19c8c99b9cfd98ee`) | 18 | **13** |
| O365 ID (`oa:AAMkAGE1M2Iy...AAA=`) | 155 | **136** |
| WhatsApp ID (`w:3EB05C4245618036EA82`) | 22 | **13** |
| ISO timestamp (`2026-02-23T22:23:26Z`) | 20 | **14** |
| Short ref (`#3`) | 2 | **2** |
| Relative time (`2h`) | 2 | **3** |
| Compact time (`Feb23 22:23`) | 11 | **6** |

Method: 50 real Gmail IDs sent in a single message, total / 50. Timestamps same.
Per-ID and per-timestamp counts confirmed with individual measurements.

## Impact on a 20-Message Listing

| | Gmail | O365 | WhatsApp |
|---|---:|---:|---:|
| IDs (real) | 260 | **2,720** | 260 |
| IDs (short refs) | 40 | 40 | 40 |
| Timestamps (ISO) | 280 | 280 | 280 |
| Timestamps (compact) | 120 | 120 | 120 |
| **Savings** | **380** | **2,840** | **380** |

O365 is catastrophic -- a single message ID (136 tokens) costs more than many
entire tool definitions. A 20-message O365 listing burns 2,720 tokens on IDs
alone, more than the entire MCP tool context (2,705 tokens).

## Proposed Solution: Session-Scoped Short Refs

Listings assign `#1`..`#N` short refs. Agent uses `get #3` to drill in.
Real platform IDs only appear in `get` response output (where the agent
actually needs them for threading, forwarding, etc.).

### Design Questions

**Scope:** Per-command (reset on every listing) vs accumulating (refs survive
across commands within a session). Per-command is simpler and safer.

**Resolution:** `get #3` resolves via the ref table. `thread #3` resolves to
the thread_id of that message. Real IDs still accepted everywhere -- short
refs are sugar, not a requirement.

**Compact timestamps:** Default in pipe format, ISO in json/xml. No config
needed. Options: relative (`2h`, `3d`), compact (`Feb23 22:23`), or
date-header grouping (print `--- Feb 23 ---` once, then just times).

### Where the Ref Table Lives

- **MCP server mode:** In-memory, per-connection. Natural session boundary.
  Each agent connection gets its own ref namespace.

- **CLI mode:** JSON file in config dir. Single-writer -- last listing wins.
  CLI has no session concept and can't distinguish multiple LLM users.
  If two agents interleave CLI listings, they'd clobber each other's refs.
  Mitigation: use `--context` flag (already exists for watermarks/stats)
  to scope refs per agent, or use MCP mode for multi-agent.

- **Skill mode:** Calls CLI under the hood, so same as CLI. Single-agent
  by nature (one Claude Code session), so single-writer is fine.

### What Stays Full-Length

- `get` response: shows real ID (agent may need it for threading/forwarding)
- `thread` response: thread ID shown once at top, messages use `#1`..`#N`
- `json` and `xml` formats: always include real IDs (machine consumption)
- Any explicit `--full` flag or equivalent

### Open Questions

- Should the ref table persist across commands? e.g. `wn` assigns `#1`..`#20`,
  then `list` assigns `#21`..`#40`? Or does every listing reset to `#1`?
  Accumulating is more powerful but risks stale refs.
- Thread-relative refs: within a `thread` response, should messages be
  `#t1`..`#tN` to avoid collision with listing refs?
- Should `overview` drill-down use refs too? e.g. `overview` shows sources
  as `#g`, `#oa`, then `overview #oa` drills in?

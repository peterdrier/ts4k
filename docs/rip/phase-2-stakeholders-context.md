# RIP Phase 2: Stakeholders & Context

**Project:** ts4k (Token Saver 4000)
**Date:** 2026-02-21
**Participants:** Test User, Claude (facilitator)

---

## Stakeholder Map

| Stakeholder | Interest | Influence | Disposition | Key Needs |
|-------------|----------|-----------|-------------|-----------|
| Peter (developer) | Build it, make it work | High | Champion | Clean architecture, fast to build, fun |
| Peter (user) | Use daily via LLM agents | High | Champion | Reliable, token-efficient, covers his platforms |
| LLM agents (consumers) | Compact, predictable interface | High | N/A | Minimal tokens, consistent format, clear errors |
| Future open-source users | Easy install, good docs, works for their platforms | Low today → High later | Unknown | pip install + docs, not Peter-specific |
| Upstream connector maintainers | They don't know ts4k exists | Medium | Neutral | If they break their API, ts4k breaks |

**100k users is a design goal** — not just aspirational. This means quality, reliability, and ease of install matter from day one. If it's good, reliable, and easy, adoption should happen organically.

---

## Current State

### Existing Connectors (as of 2026-02-21)

| Connector | Type | Source | Transport | Tools Exposed | Mode |
|-----------|------|--------|-----------|---------------|------|
| Gmail (Google Workspace) | Python FastMCP | user/google_workspace_mcp (fork) | HTTP on :51429 | 4 enabled: search, read, batch read, labels | Read-only (`gmail:organize`) |
| WhatsApp | Go bridge + Python FastMCP | whatsapp-mcp (local) | stdio + Go bridge on :18741 | 9 tools: contacts, messages, chats, history sync | Read-only (send removed) |
| Google Calendar | Node.js | @cocal/google-calendar-mcp | npx/stdio | 6 tools: list, search, get, freebusy | Read-only |
| O365 | *nothing yet* | — | — | — | — |

### Gmail MCP Detail (user/google_workspace_mcp)

Fork of taylorwilsdon/google_workspace_mcp. Full Google Workspace MCP server covering Gmail, Calendar, Drive, Docs, Sheets, Chat, Forms, Tasks, Contacts, Search, Apps Script.

**Gmail tools (15 across tiers):**

| Tier | Tool | Purpose |
|------|------|---------|
| Core | `search_gmail_messages` | Search Gmail with query syntax, paginated results |
| Core | `get_gmail_message_content` | Full content of single message by ID |
| Core | `get_gmail_messages_content_batch` | Batch fetch up to 25 messages |
| Core | `send_gmail_message` | Send email (disabled in current config) |
| Extended | `get_gmail_thread_content` | All messages in a thread |
| Extended | `get_gmail_attachment_content` | Download attachment (base64) |
| Extended | `modify_gmail_message_labels` | Add/remove labels |
| Extended | `list_gmail_labels` | List all labels |
| Extended | `manage_gmail_label` | Create/update/delete labels |
| Extended | `draft_gmail_message` | Create draft |
| Extended | `list_gmail_filters` / `create_gmail_filter` / `delete_gmail_filter` | Filter management |
| Complete | `get_gmail_threads_content_batch` | Batch fetch multiple threads |
| Complete | `batch_modify_gmail_message_labels` | Batch label modification |

**Currently enabled in life-os:** search_gmail_messages, get_gmail_message_content, get_gmail_messages_content_batch, list_gmail_labels

### WhatsApp MCP Detail

Two-component system:
- **Go bridge** (whatsmeow): Connects to WhatsApp Web, stores messages in SQLite (`messages.db`)
- **Python FastMCP server**: Read-only access to SQLite

**Tools (9, read-only):**
- `search_contacts`, `list_messages`, `list_chats`, `get_chat`, `get_direct_chat_by_contact`, `get_contact_chats`, `get_last_interaction`, `get_message_context`, `request_history_sync`, `download_media`

**Send tools intentionally removed** to prevent "lethal trifecta" (read + tool-use + exfiltration).

**Local data:** ~6-9 months of history in SQLite. Older history on physical phone, needs export/import.

### What Exists Today (the pain point)

Two nights ago, Peter loaded Gmail + WhatsApp + Discord MCPs into an agent and saw tokens burned at an unsustainable rate. Each MCP adds 1-2k+ tokens of tool definitions to context, and raw message content (HTML emails, verbose JSON) burns 10x more per read. This is the direct trigger for ts4k.

### Configuration Location

- MCP config: `~/.config/mcp.json`
- Permissions: `~/.claude/settings.local.json`
- OAuth: `~/.claude/google-oauth.keys.json`

---

## Constraints

### Hard Constraints

| Constraint | Impact |
|------------|--------|
| Must run on Ubuntu NUC (always-on deployment target) | Architecture must be Linux-native |
| CLI should work on Windows and Mac too | Cross-platform CLI, platform-specific bits isolated |
| WhatsApp bridge must run 24/7 | Runtime dependency, NUC is the right host |
| Use latest LTS versions of all tooling | Limits bleeding-edge choices |
| Wraps existing connectors, doesn't reimplement platform APIs | Adapter design is "MCP client" not "API client" |
| Designed for 100k users from day one | No Peter-specific config baked in, good defaults, good docs |

### Soft Constraints

| Constraint | Flexibility |
|------------|-------------|
| Side project, no hard deadline | But should be buildable in days once planned |
| Stack decision deferred to Phase 4 | 3 options with reasoning, Peter picks |
| Fork upstream connectors if needed | Pragmatic, don't wait for upstream |
| Public GitHub from day one | MIT or similar, structured for community |

---

## Dependencies & External Factors

| Dependency | Nature | Risk | Mitigation |
|------------|--------|------|------------|
| user/google_workspace_mcp | Fork, Peter controls | Low | Can modify freely |
| whatsapp-mcp Go bridge | Local, Peter controls | Low | But Go bridge is a runtime dependency |
| WhatsApp Web protocol (whatsmeow) | Reverse-engineered protocol | **Medium** | Meta could break at any time; no mitigation except community response |
| O365 connector (TBD) | Doesn't exist yet | **Research needed** | Must evaluate MS Graph MCP servers |
| Google OAuth credentials | Already configured | Low | Shared project ID across tools |
| MCP protocol/SDK | Official, actively maintained | Low | Anthropic-backed |

---

## Key Findings

1. **The existing plan's mention of `gog` CLI is wrong.** The actual Gmail connector is google_workspace_mcp — a full MCP server. Better fit for ts4k since it can be MCP-client-to-MCP-server.

2. **Security posture is already thoughtful.** Send disabled on WhatsApp, Gmail locked to `organize`. ts4k's safety rails align with decisions already made.

3. **WhatsApp has local SQLite store.** Bulk historical for WhatsApp may be simpler (read local DB) vs. Gmail/O365 (fetch from server).

4. **Google Workspace MCP is huge** (40+ tools). ts4k only needs a handful for Gmail, but Calendar/Drive/Docs could feed into "whatsnew" later.

5. **Three different runtimes** in current setup (Python, Go, Node.js). ts4k wrapping these needs to handle heterogeneity.

6. **ts4k is not just a messaging gateway.** It's a "what's changed" funnel — any data source with a feed of updates is a valid adapter target. Day one is mail/messaging; calendar, Jira, GitHub, Drive changes are all viable later.

---

## Decisions Made in Phase 2

| # | Decision | Rationale |
|---|----------|-----------|
| 10 | Fork-friendly stance on upstream connectors | Don't wait for others. Fork if needed. |
| 11 | NUC is deployment target (Ubuntu, always-on). Dev on Windows. | WhatsApp bridge needs 24/7. Other machines don't run all the time. |
| 12 | No hard deadline. Buildable in days once planned. | Side project, but fast execution expected. |
| 13 | ts4k is a "what's changed" funnel, not just messaging | Mail day 1, calendar day 5, drives day 20, Jira/GitHub/anything later. Adapter interface must be generic. |
| 14 | 100k users is a design goal | Easy install, good docs, reliable defaults. Not Peter-specific. |
| 15 | O365 is day 2 but surface area research needed during planning | Must evaluate MS Graph MCP options. |
| 16 | Bulk historical is a batching problem | WhatsApp: local SQLite. Gmail/O365: server-side fetch with pagination. Per-person or chronological strategies. |

---

## Open Questions (to resolve in later phases)

- What MS Graph MCP servers exist and what do they expose?
- Stack decision: Python vs alternatives (3 options in Phase 4)
- How does ts4k talk to upstream MCPs? (MCP client? subprocess? direct library import?)
- WhatsApp phone export/import workflow for historical data
- Adapter interface design — generic enough for non-messaging sources
- How to handle the three different upstream runtimes (Python, Go, Node.js)

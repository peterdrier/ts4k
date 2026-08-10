# Usage Guide

Complete reference for ts4k CLI commands and MCP server mode.

## Quick Reference

| Command | Alias | Description |
|---------|-------|-------------|
| `ts4k updates` | `wn` | What's new (updates watermark) |
| `ts4k list` | `l` | Search messages |
| `ts4k get ID` | `g` | Read a single message |
| `ts4k thread TID` | `t` | Read a thread/conversation |
| `ts4k overview` | `o` | Hierarchical overview of cached messages |
| `ts4k status` | `st` | Health, stats, token savings |
| `ts4k help` | `h` | Quick reference + source status |

## Global Flags

| Flag | Description |
|------|-------------|
| `-f`, `--format` | Output format: `pipe` (default), `json`, `xml` (or `p`, `j`, `x`) |
| `-F`, `--filter` | Apply skip filters (off by default) |
| `-n`, `--count` | Max messages to return |
| `-v`, `--verbose` | Enable debug logging |

## Message IDs

Every message has a platform-prefixed ID:

| Prefix | Platform | Example |
|--------|----------|---------|
| `g:` | Gmail | `g:19abc123def` |
| `o:` | O365 | `o:AAMkADRiYTg5...` |
| `w:` | WhatsApp | `w:3EB05C4245618036` |

Use these IDs with `get`, `thread`, and other commands.

---

## Commands

### updates (wn) — What's New

Fetches new messages since the last check. Updates the watermark so the next call only returns newer messages.

```bash
ts4k wn                          # All sources
ts4k wn --source g               # Gmail only
ts4k wn --source g,o             # Gmail + O365
ts4k wn --since 2d               # Last 2 days (ignores watermark)
ts4k wn --since 2026-02-20       # Since specific date
ts4k wn -n 5                     # Max 5 messages
ts4k wn -F                       # Apply skip filters
ts4k wn -f json                  # JSON output
```

Example output (pipe format):

```
SOURCE|FROM|SUBJECT|DATE|ID|SIZE
g|alice@acme.com|Q1 report draft|2026-02-24T09:15:00Z|g:19abc123|2.1kb
o|carol@contoso.com|Budget approval|2026-02-24T10:00:00Z|o:AAMkAD789|1.8kb
w|+15551234567|Hey, running late|2026-02-24T09:45:00Z|w:3EB05C42|120b
```

### list (l) — Search Messages

Search without updating the watermark. Good for exploring history.

```bash
ts4k l -q "from:alice"           # Gmail query syntax
ts4k l -q "quarterly report" --source o   # O365 search
ts4k l --source g -n 50          # Last 50 Gmail messages
ts4k l --source g --contact alice # Filter by linked contact
```

### get (g) — Read a Message

Fetch and display a single message by ID.

```bash
ts4k g g:19abc123def             # Read a Gmail message
ts4k g o:AAMkADRiYTg5           # Read an O365 message
ts4k g w:3EB05C42               # Read a WhatsApp message
ts4k g g:19abc123 -f json        # JSON output
```

### thread (t) — Read a Thread

Read an entire conversation thread.

```bash
ts4k t g:thread_abc123           # Gmail thread
ts4k t w:120363025@g.us          # WhatsApp group chat
ts4k t o:AAMkADthread            # O365 conversation
```

### overview (o) — Cache Overview

Instant, cache-only summary of your message history. No network calls.

```bash
ts4k o                           # Top-level: by source
ts4k o --source g                # Drill into Gmail: top senders, threads
ts4k o --contact alice           # Drill into a contact: by source, by quarter
ts4k o --period 2025-Q4          # Filter to a time period
ts4k o --period 2025-01..2025-06 # Explicit date range
ts4k o --source g --contact alice --period 2026  # Combine filters
ts4k o -n 20                     # Top 20 senders/threads
```

Requires cached data. Run `ts4k preload` first to populate the cache.

### status (st) — Health and Stats

```bash
ts4k st
```

Shows: configured sources, connectivity, watermark positions, message counts, byte savings, and token efficiency percentage.

### help (h) — Quick Reference

```bash
ts4k h
```

Shows source status and a compact command reference.

---

## Source Management

### List Sources

```bash
ts4k src list
```

### Add a Source

```bash
ts4k src add g  gmail    email=alice@gmail.com
ts4k src add gw gmail    email=alice@work.com
ts4k src add o  o365     client_id=abc123 tenant_id=common
ts4k src add ow o365     client_id=abc123 tenant_id=common mailbox=peter@work.com
ts4k src add w  whatsapp mcp_cwd=/path/to/whatsapp-mcp/server
```

Prefixes are your choice — use whatever short labels make sense.

### Remove a Source

```bash
ts4k src rm gw
```

### Discover O365 Mailboxes

```bash
ts4k src discover
```

---

## Authentication

### Gmail

```bash
ts4k auth g                    # OAuth flow (opens browser)
ts4k auth g --check            # Verify credentials
```

### O365

```bash
ts4k auth o                    # Device code flow
ts4k auth o --check            # Verify credentials
```

WhatsApp has no authentication step — it reads from a local database.

---

## Contacts

Cross-platform identity map. Link the same person's identifiers across platforms so ts4k can group their messages.

```bash
ts4k c link alice g:alice@gmail.com w:15551234567@s.whatsapp.net o:alice@work.com
ts4k c list                      # Show all contacts
ts4k c find alice                # Search by name or identifier
ts4k c unlink alice w:15551234567@s.whatsapp.net   # Remove one identifier
ts4k c unlink alice              # Remove entire contact
```

Once linked, `--contact alice` works across all platforms:

```bash
ts4k l --contact alice           # Messages from alice on all sources
ts4k o --contact alice           # Overview for alice
```

### Seeding the map from an address book (CardDAV)

Rather than linking everyone by hand, import an iCloud address book over
CardDAV. Setup — including the Apple app-specific password — is in the
[README](../README.md#apple--icloud-contacts-carddav).

```bash
ts4k src add ic apple-contacts email=you@icloud.com   # one-time
ts4k c sync                      # preview: proposed links, conflicts, skips
ts4k c sync --apply              # commit the proposed links
ts4k c sync --source ic --apply  # pick a source when several are configured
```

`ts4k c sync` writes nothing without `--apply`. Each vCard becomes one alias
(the display name, lowercased) holding `g:<email>` for every email address and
`w:<E164>@s.whatsapp.net` for every phone number in international format.

Two things are reported and left alone rather than merged:

- **an alias that already exists** — your hand-made version wins
- **an identifier another alias already owns** — importing it would give one
  person two aliases

Skipped records are counted by reason: no name, no phone or email, already up
to date, and phone numbers with no country code (ts4k does not guess one).

The import is read-only and one-way: nothing is written back to iCloud, and a
CardDAV source never appears in `whatsnew`, `list`, the message cache, or
watermarks.

---

## Filters

Skip lists for low-value messages. **Off by default** — use `-F` to apply them. This preserves the triage use case where you need to see everything.

```bash
ts4k f show                      # Current filter config

ts4k f add-sender noreply@linkedin.com
ts4k f rm-sender noreply@linkedin.com

ts4k f add-domain marketing.example.com
ts4k f rm-domain marketing.example.com

ts4k f add-pattern "unsubscribe.*click here"
ts4k f rm-pattern "unsubscribe.*click here"

ts4k f skip-groups true          # Skip WhatsApp group messages
ts4k f skip-groups false

ts4k f reset                     # Clear all filters
```

Apply filters with any command:

```bash
ts4k wn -F                       # What's new, filtered
ts4k l -q "from:alice" -F        # Search, filtered
```

---

## Cache and Preload

ts4k caches messages locally for Gmail and O365 (network-heavy sources). WhatsApp reads from a local database and is not cached.

### Cache Stats

```bash
ts4k cache stats
```

### Preload History

Paginate through your message history and cache it locally. Required for `overview` to have data.

```bash
ts4k preload --source g                           # Gmail, 100 pages
ts4k preload --source o --max-pages 50            # O365, 50 pages
ts4k preload --source g --query "from:alice"      # Filtered preload
ts4k preload --source g --contact alice           # Preload by contact
ts4k preload --source g --since 2025-01-01        # Since date
ts4k preload --source g --bodies                  # Cache full message bodies too
ts4k preload --source g --page-size 50            # Messages per page
ts4k preload --source g --throttle 1.0            # Seconds between pages
```

### Background Preload

For large mailboxes, run preload in the background:

```bash
ts4k preload --source g --bg                      # Start in background
ts4k preload --status                             # Check progress
ts4k preload --cancel job-1708600000              # Kill a running job
ts4k preload --resume job-1708600000              # Resume a stopped job
```

### Clear Cache

```bash
ts4k cache clear                                  # Everything
ts4k cache clear --source g                       # Gmail only
ts4k cache clear --stale                          # Only outdated entries
```

---

## Output Formats

### Pipe-delimited (default)

Optimized for LLM consumption. ~60% fewer tokens than JSON.

```
SOURCE|FROM|SUBJECT|DATE|ID|SIZE
g|alice@acme.com|Q1 report|2026-02-24T09:15:00Z|g:19abc|2.1kb
```

### JSON

```bash
ts4k wn -f json
```

```json
[
  {
    "source": "g",
    "from": "alice@acme.com",
    "subject": "Q1 report",
    "date": "2026-02-24T09:15:00Z",
    "id": "g:19abc",
    "size": "2.1kb"
  }
]
```

### XML

```bash
ts4k wn -f xml
```

Compact mini-XML format designed for LLM readability.

---

## MCP Server Mode

ts4k exposes all commands as MCP tools for Claude Code and other MCP-compatible agents.

### Start the Server

```bash
ts4k-mcp                                         # stdio (default)
ts4k-mcp --transport http                         # HTTP on port 8000
ts4k-mcp --transport http --port 9000             # Custom port
```

### Claude Code Integration

Add to your Claude Code MCP config:

```json
{
  "mcpServers": {
    "ts4k": {
      "command": "ts4k-mcp",
      "args": []
    }
  }
}
```

Or with context scoping (separate watermarks per agent session):

```json
{
  "mcpServers": {
    "ts4k": {
      "command": "ts4k-mcp",
      "args": ["--context", "claude-code"]
    }
  }
}
```

### Available MCP Tools

| Tool | Maps to |
|------|---------|
| `updates` | `ts4k wn` |
| `get` | `ts4k g` |
| `thread` | `ts4k t` |
| `list` | `ts4k l` |
| `overview` | `ts4k o` |
| `status` | `ts4k st` |
| `contacts` | `ts4k c` |
| `cache` | `ts4k cache` |
| `preload` | `ts4k preload` |
| `preload_status` | `ts4k preload --status` |
| `filter` | `ts4k f` |

### Context Scoping

```bash
ts4k-mcp --context agent-morning
```

Scopes watermarks and stats to `~/.config/ts4k/contexts/agent-morning/`, so multiple agents or sessions can have independent "last read" positions. Sources, contacts, and filters remain global.

---

## Skill Mode

ts4k includes a Claude Code skill for compact, token-efficient command routing:

```bash
ts4k skill                       # Basic command reference
ts4k skill more                  # Admin commands
ts4k skill wn --source g         # Route to any command
```

The skill mode returns minimal output optimized for LLM context windows.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TS4K_CONFIG_DIR` | `~/.config/ts4k` | Override config/state directory |
| `TS4K_TIMEZONE` | the machine's zone | Display timezone for calendar times (IANA name) |

### Display timezone

Calendar events are stored and compared in UTC, and converted to a **single
display timezone** when rendered — one zone for the whole agenda, so a listing
merged from calendars in different zones does not mix offsets. Event times and
the `today` / `tomorrow` / `week` day boundaries all follow it.

Resolution order: `TS4K_TIMEZONE`, then `"timezone"` in
`~/.config/ts4k/settings.json`, then the machine's own zone.

```json
{
  "timezone": "Europe/Amsterdam"
}
```

All-day events are dates rather than instants, so they are never shifted.

---

## Examples

### Morning email triage (agent workflow)

```bash
ts4k wn                          # See what's new across all sources
ts4k g g:19abc123                # Read the important-looking one
ts4k t g:thread456               # Read the full thread for context
```

### Preload and explore a mailbox

```bash
ts4k preload --source g --bg     # Cache Gmail history in background
ts4k preload --status            # Check progress
ts4k o                           # Top-level overview
ts4k o --source g                # Top senders and threads
ts4k o --contact alice           # Everything from alice
ts4k o --period 2025-Q4          # Q4 2025 breakdown
```

### Set up cross-platform contact tracking

```bash
ts4k c link alice g:alice@gmail.com o:alice@work.com w:15551234567@s.whatsapp.net
ts4k l --contact alice           # All of alice's messages, any platform
ts4k o --contact alice           # Overview across sources
```

### Filter out noise

```bash
ts4k f add-sender noreply@github.com
ts4k f add-domain marketing.example.com
ts4k f add-pattern "unsubscribe"
ts4k wn -F                       # Filtered results
```

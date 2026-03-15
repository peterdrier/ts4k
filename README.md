# ts4k — Token Saver 4000

Unified messaging gateway that gives LLM agents token-efficient access to messages across Gmail, O365, and WhatsApp. Normalizes, deduplicates, filters, and formats messages for LLM context windows.

Raw HTML email: ~8,000 tokens. After ts4k: ~400 tokens. **20x reduction.**

## Install

```bash
pip install ts4k
```

Requires Python 3.12+. Creates two entry points:
- `ts4k` — CLI
- `ts4k-mcp` — MCP server for Claude Code and other MCP-compatible agents

## Getting Started

### 1. Add a provider

Each messaging platform needs a one-time setup. One provider is enough to get started — add more later.

| Provider | Guide | Auth Method |
|----------|-------|-------------|
| Gmail | [docs/setup-gmail.md](docs/setup-gmail.md) | Google OAuth (browser) |
| O365 | [docs/setup-o365.md](docs/setup-o365.md) | Azure device code flow |
| WhatsApp | [docs/setup-whatsapp.md](docs/setup-whatsapp.md) | Local SQLite (no auth) |

### 2. Check it works

```
$ ts4k status --live
Sources:
  g   gmail     you@gmail.com        [ok]
  o   o365      you@company.com      [ok]
  w   whatsapp  /path/to/whatsapp    [ok]

Mailbox (g, gmail):
  LABEL|TOTAL|UNREAD
  Inbox|1423|42
  Primary|890|12
```

Each source gets a short ID (`g`, `o`, `w`) that you use in commands to target that account.

### 3. Read your messages

```
$ ts4k whatsnew life
1|g|alice@acme.com|Q1 report draft|2026-02-24T09:15|2.1kb
2|g|bob@example.com|Re: lunch tomorrow|2026-02-24T08:30|340b
3|o|carol@contoso.com|Budget approval|2026-02-24T10:00|1.8kb
4|w|+15551234567|Hey, running late|2026-02-24T09:45|120b
```

One command, all platforms, pipe-delimited output an LLM can parse in ~60 tokens. The ref numbers (1, 2, ...) let you drill in:

```bash
ts4k get 1           # Read full message
ts4k thread 2        # Read full thread
```

## Usage

### Reading messages

The key (e.g. `life`, `work`) in `whatsnew` names a watermark — ts4k remembers the last timestamp per key so each call returns only new messages. The key also scopes ref-number mapping, so `get 1` resolves to the right message from that session.

```bash
ts4k whatsnew life               # New messages since last check (keyed watermark)
ts4k updates --since 2d          # Messages from last 2 days (stateless)
ts4k updates --source g          # Only this source (e.g. your Gmail account "g")
ts4k get g:19abc123              # Read message by native ID
ts4k list -q "budget" -n 10     # Search messages
ts4k overview                    # Hierarchical cache summary
```

### Calendar

Google Calendar shares OAuth with Gmail; O365 Calendar shares with O365 mail.

```bash
ts4k cal setup                   # Discover and add calendar sources
ts4k cal                         # Today's events
ts4k cal tomorrow                # Tomorrow's events
ts4k cal week                    # This week (Mon-Sun)
ts4k cal next 5                  # Next 5 events
ts4k cal range --from 2026-04-01 --to 2026-04-07
ts4k cal event 3                 # Full detail for ref #3
```

### Managing messages

Requires `modify` [access level](#access-levels) or higher. All actions are non-destructive and reversible.

```bash
ts4k manage archive g:abc123              # Archive a message
ts4k manage archive g:abc,g:def,g:ghi    # Batch archive
ts4k manage label g:abc --label invoices  # Add a label
ts4k manage read g:abc,g:def             # Mark as read
ts4k manage trash g:abc                   # Move to trash (recoverable)
ts4k manage list-labels g:any             # List available labels
ts4k manage archive g:abc --dry-run       # Preview without acting
```

### Creating drafts

Requires `draft` [access level](#access-levels). ts4k **never sends messages** — drafts appear in your mailbox for manual review.

```bash
ts4k draft create -s g --to alice@x.com --subject "Hi" --body "Hello"
ts4k draft create -s g --reply-to g:abc123 --body "Sounds good!"  # Threaded reply
```

Reply drafts automatically set threading headers and blockquote the original message.

### Access levels

Each source has a permission level that controls what's allowed. The default is `readonly` — you opt in to more:

| Level | Email | Calendar |
|-------|-------|----------|
| `readonly` | Read, list, search | View events |
| `modify` | + archive, label, mark read/unread, trash | + RSVP |
| `draft` | + create draft messages | + create events (no attendees) |
| `send` | *(not implemented)* | + create events with attendees |

Set the level when adding a source:

```bash
ts4k src add g gmail email=you@gmail.com level=draft
```

Changing the level triggers re-authentication with the correct OAuth scopes.

## Integrations

### Claude Code (skill)

Install the ts4k skill so Claude Code automatically uses ts4k for email and calendar tasks:

```bash
ts4k skill install           # global: ~/.claude/skills/ts/
ts4k skill install --project  # project-level: .claude/skills/ts/
```

### MCP Server

For Claude Code and other MCP-compatible agents:

```bash
ts4k-mcp                              # stdio (default, for Claude Code)
ts4k-mcp --transport http --port 9000  # HTTP for other clients
```

13 MCP tools: `updates`, `whatsnew`, `get`, `thread`, `list`, `overview`, `status`, `admin`, `manage`, `draft`, `cal`, `cal_create`, `cal_manage`.

## Architecture

```
Agent (Claude, etc.)
  |
  v
ts4k (normalize → filter → format)
  |── Gmail Adapter     → Google Gmail API (direct)
  |── O365 Adapter      → Microsoft Graph API (direct, httpx)
  |── WhatsApp Adapter  → whatsapp-mcp (local SQLite)
  |── GCal Adapter      → Google Calendar API (direct)
  |── O365 Cal Adapter  → Microsoft Graph Calendar API (direct, httpx)
  '── Future adapters   → Slack, Teams, Telegram, ...
```

- **Adapters** wrap platform APIs with a uniform interface, gated by access level.
- **Normalize** strips HTML, deduplicates reply chains, collapses whitespace.
- **Filter** applies skip lists (senders, domains, patterns). Off by default.
- **Format** outputs pipe-delimited (default), JSON, or XML.

Platform failures are isolated — if one adapter is down, the others still return results.

## Design Principles

- **Metadata first, content on demand** — default to minimum useful response
- **No LLM calls inside ts4k** — this is the data layer, not the intelligence layer
- **ts4k never sends messages** — draft creation only
- **Using a command IS the side effect** — watermarks update on `whatsnew`, no separate save step
- **Format is a feature** — pipe-delimited saves ~60% tokens vs JSON

## Configuration

All state lives in `~/.config/ts4k/` (override with `TS4K_CONFIG_DIR`):

```
~/.config/ts4k/
  sources.json              # Configured providers (incl. access levels)
  watermarks/               # Per-key last-fetched timestamps
  contacts.json             # Cross-platform identity map
  filters.json              # Skip lists
  stats.json                # Usage counters
  cache/                    # Local message cache
  google/{email}/token.json # Gmail OAuth tokens
  microsoft/{id}/token_cache.json  # O365 MSAL tokens
```

## License

MIT

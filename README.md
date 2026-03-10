# ts4k — Token Saver 4000

Unified messaging gateway that gives LLM agents token-efficient access to messages across Gmail, O365, and WhatsApp. Normalizes, deduplicates, filters, and formats messages for LLM context windows.

Raw HTML email: ~8,000 tokens. After ts4k: ~400 tokens. **20x reduction.**

## Quick Example

```
$ ts4k whatsnew life
1|g|alice@acme.com|Q1 report draft|2026-02-24T09:15|2.1kb
2|g|bob@example.com|Re: lunch tomorrow|2026-02-24T08:30|340b
3|o|carol@contoso.com|Budget approval|2026-02-24T10:00|1.8kb
4|w|+15551234567|Hey, running late|2026-02-24T09:45|120b
```

One command, three platforms, pipe-delimited output an LLM can parse in ~60 tokens. Ref numbers (1, 2, ...) let you `get 1` or `thread 2` without copying long IDs.

## Install

```bash
pip install ts4k
```

Requires Python 3.12+. Creates two entry points:
- `ts4k` — CLI
- `ts4k-mcp` — MCP server for Claude Code and other MCP-compatible agents

## Provider Setup

Each messaging platform needs a one-time setup. Pick the ones you use:

| Provider | Guide | Auth Method | Complexity |
|----------|-------|-------------|------------|
| Gmail | [docs/setup-gmail.md](docs/setup-gmail.md) | Google OAuth (browser) | Medium |
| O365 | [docs/setup-o365.md](docs/setup-o365.md) | Azure device code flow | Medium |
| WhatsApp | [docs/setup-whatsapp.md](docs/setup-whatsapp.md) | Local SQLite (no auth) | Low |

You can run any combination. One provider is enough to get started.

## Usage

### Reading Messages

```bash
ts4k whatsnew life               # New messages since last check (keyed watermark)
ts4k updates --since 2d          # Messages from last 2 days (stateless)
ts4k updates --source g          # Gmail only
ts4k get 3                       # Read message by ref number
ts4k get g:19abc123              # Read message by full ID
ts4k thread g:thread456          # Read a thread
ts4k list -q "budget" -n 10     # Search messages
ts4k overview                    # Hierarchical cache summary
ts4k status --live               # Health + live mailbox counts
```

### Managing Messages

Requires source level `modify` or higher. All actions are non-destructive and reversible.

```bash
ts4k manage archive g:abc123              # Archive a message
ts4k manage archive g:abc,g:def,g:ghi    # Batch archive
ts4k manage label g:abc --label invoices  # Add a label
ts4k manage read g:abc,g:def             # Mark as read
ts4k manage trash g:abc                   # Move to trash (recoverable)
ts4k manage list-labels g:any             # List available labels
ts4k manage archive g:abc --dry-run       # Preview without acting
```

### Creating Drafts

Requires source level `draft`. ts4k **never sends messages** — drafts appear in your mailbox for manual review.

```bash
ts4k draft create -s g --to alice@x.com --subject "Hi" --body "Hello"
ts4k draft create -s g --reply-to g:abc123 --body "Sounds good!"  # Threaded reply
```

Reply drafts automatically set threading headers and blockquote the original message.

### Access Levels

Each source has a permission level that controls what operations are allowed:

| Level | Capabilities | OAuth Scope |
|-------|-------------|-------------|
| `readonly` (default) | Read, list, search | `gmail.readonly` / `Mail.Read` |
| `modify` | + archive, label, mark read/unread, trash | `gmail.modify` / `Mail.ReadWrite` |
| `draft` | + create draft messages | `gmail.modify` / `Mail.ReadWrite` |

Set the level when adding or updating a source:

```bash
ts4k src add g gmail email=you@gmail.com level=draft
```

Changing the level automatically triggers re-authentication with the correct OAuth scopes.

### MCP Server Mode

```bash
ts4k-mcp                              # stdio (default, for Claude Code)
ts4k-mcp --transport http --port 9000  # HTTP for other clients
```

10 MCP tools: `updates`, `whatsnew`, `get`, `thread`, `list`, `overview`, `status`, `admin`, `manage`, `draft`.

## Architecture

```
Agent (Claude, etc.)
  |
  v
ts4k (normalize → filter → format)
  |── Gmail Adapter    → Google Gmail API (direct)
  |── O365 Adapter     → Microsoft Graph API (direct, httpx)
  |── WhatsApp Adapter → whatsapp-mcp (local SQLite)
  '── Future adapters  → Slack, Teams, Telegram, ...
```

- **Adapters** wrap platform APIs with a uniform interface. Each adapter supports read, manage, and draft operations gated by access level.
- **Normalize** strips HTML, deduplicates reply chains, collapses whitespace.
- **Filter** applies skip lists (senders, domains, patterns). Off by default.
- **Format** outputs pipe-delimited (default), JSON, or XML.

Platform failures are isolated — if one adapter is down, the others still return results.

## Key Principles

- **Metadata first, content on demand** — default to minimum useful response
- **No LLM calls inside ts4k** — this is the data layer, not the intelligence layer
- **ts4k never sends messages** — draft creation only; the send level is defined but intentionally not implemented
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

## Tech Stack

Python 3.12+, Google API client, MSAL, httpx, MCP SDK, html2text, beautifulsoup4.

## License

MIT

# ts4k — Token Saver 4000

Unified messaging gateway that gives LLM agents token-efficient access to messages across Gmail, O365, and WhatsApp. Normalizes, deduplicates, filters, and formats messages for LLM context windows.

Raw HTML email: ~8,000 tokens. After ts4k: ~400 tokens. **20x reduction.**

## Quick Example

```
$ ts4k wn
SOURCE|FROM|SUBJECT|DATE|ID|SIZE
g|alice@acme.com|Q1 report draft|2026-02-24T09:15:00Z|g:19abc123|2.1kb
g|bob@example.com|Re: lunch tomorrow|2026-02-24T08:30:00Z|g:19def456|340b
o|carol@contoso.com|Budget approval|2026-02-24T10:00:00Z|o:AAMkAD789|1.8kb
w|+15551234567|Hey, running late|2026-02-24T09:45:00Z|w:3EB05C42|120b
```

One command, three platforms, pipe-delimited output an LLM can parse in ~60 tokens.

## Install

```bash
pip install -e .
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

See [docs/usage.md](docs/usage.md) for the full command reference, MCP server mode, output formats, and examples.

### Everyday Commands

```bash
ts4k wn                        # What's new across all sources
ts4k wn --source g             # Gmail only
ts4k g g:19abc123              # Read a specific message
ts4k t g:thread456             # Read a thread
ts4k l -q "from:alice" -n 10   # Search messages
ts4k o                         # Overview of cached messages
ts4k st                        # Health + token savings stats
```

### MCP Server Mode

```bash
ts4k-mcp                              # stdio (default, for Claude Code)
ts4k-mcp --transport http --port 9000  # HTTP for other clients
```

Exposes the same commands as MCP tools: `updates`, `get`, `thread`, `list`, `overview`, `status`, and more.

## Architecture

```
Agent (Claude, etc.)
  |
  v
ts4k (normalize --> filter --> format)
  |-- Gmail Adapter    --> Google Gmail API
  |-- O365 Adapter     --> Microsoft Graph API
  |-- WhatsApp Adapter --> whatsapp-mcp (local SQLite)
  '-- Future adapters  --> Slack, Teams, Telegram, ...
```

- **Adapters** wrap platform APIs. ts4k doesn't reimplement them.
- **Normalize** strips HTML, deduplicates reply chains, collapses whitespace.
- **Filter** applies skip lists (senders, domains, patterns). Off by default.
- **Format** outputs pipe-delimited (default), JSON, or XML.

Platform failures are isolated — if one adapter is down, the others still return results.

## Key Principles

- **Metadata first, content on demand** — default to minimum useful response
- **No LLM calls inside ts4k** — this is the data layer, not the intelligence layer
- **Using a command IS the side effect** — watermarks update on `whatsnew`, no separate save step
- **Format is a feature** — pipe-delimited saves ~60% tokens vs JSON

## Configuration

All state lives in `~/.config/ts4k/` (override with `TS4K_CONFIG_DIR`):

```
~/.config/ts4k/
  sources.json              # Configured providers
  watermarks.json           # Last-fetched timestamps
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

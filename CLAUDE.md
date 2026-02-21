# ts4k — Claude Code Instructions

## What This Project Is

ts4k (Token Saver 4000) is a Python CLI + MCP server that gives LLM agents token-efficient, unified access to messages across Gmail, WhatsApp, Telegram, and future platforms. It normalizes, filters, and compactly formats messages so downstream agents spend hundreds of tokens instead of thousands per interaction.

**ts4k does not analyze or act on messages.** It retrieves, normalizes, and delivers. All intelligence belongs to the consuming agent.

## Project Plan

Read `docs/plan-v1.md` for the full architecture, design principles, output format specs, and phased implementation roadmap. That document is the source of truth for what ts4k should become.

## Architecture at a Glance

- **Adapters** wrap existing tools (gog CLI for Gmail, WhatsApp MCP, Telegram API). ts4k does not reimplement platform APIs.
- **Normalize → Filter → Format** pipeline processes every message before output.
- **Output formats**: pipe-delimited for listings, mini XML for message bodies. Optimized for LLM token efficiency.
- **State**: JSON files (watermarks, contact identity map, filter config). Git-friendly, human-readable.
- **Two modes**: CLI (`ts4k whatsnew`) and MCP server (same commands as MCP tools).

## Tech Stack

- Python (3.11+)
- Official Python MCP SDK for server mode
- html2text / beautifulsoup4 for HTML processing
- JSON state files in `~/.config/ts4k/`
- pip-installable with CLI + MCP server entry points

## Key Design Rules

1. **Metadata first, content on demand.** Default to minimum useful response. Agent opts into `--full`, `--tail N`, etc.
2. **No LLM calls inside ts4k.** This is the data layer, not the intelligence layer.
3. **Platform failures are isolated.** If one adapter is down, others still return results.
4. **Native platform IDs** prefixed with source (`g:`, `w:`, `t:`). No synthetic sequential IDs.
5. **Using a command IS the side effect.** Watermarks update on `whatsnew`. No separate save step.
6. **Format is a feature.** Pipe-delimited for listings (~60% savings over JSON), mini XML for bodies.

## Implementation Phases

1. **Phase 1**: Gmail adapter + core (whatsnew, list, read, thread, normalization, watermarks, CLI)
2. **Phase 2**: MCP server mode, history command, filter config, contacts, status
3. **Phase 3**: WhatsApp + Telegram adapters, cross-platform merge, contact identity map
4. **Phase 4**: Send/draft passthrough, thread noise reduction, search, pagination, perf

## Repository Layout (planned)

```
ts4k/
├── docs/
│   └── plan-v1.md          # Full project plan
├── src/ts4k/
│   ├── __init__.py
│   ├── cli.py               # CLI entry point
│   ├── server.py            # MCP server entry point
│   ├── core/
│   │   ├── normalize.py     # HTML stripping, reply chain dedup, whitespace collapse
│   │   ├── filter.py        # Skip lists, category filters, trivial message filter
│   │   └── format.py        # Pipe-delimited, mini XML, mixed output
│   ├── adapters/
│   │   ├── base.py          # BaseAdapter interface
│   │   ├── gmail.py         # Gmail adapter (wraps gog CLI)
│   │   ├── whatsapp.py      # WhatsApp adapter
│   │   └── telegram.py      # Telegram adapter
│   └── state/
│       ├── watermarks.py    # Per-platform processing timestamps
│       ├── contacts.py      # Cross-platform identity map
│       └── config.py        # Filter config, skip lists
├── tests/
├── pyproject.toml
└── README.md
```

## Deployment Target

NUC running Linux. Also usable on Windows (Peter's dev machine) during development.

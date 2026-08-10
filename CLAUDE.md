# ts4k — Claude Code Instructions

## What This Project Is

ts4k (Token Saver 4000) is a Python CLI + MCP server that gives LLM agents token-efficient, unified access to messages across Gmail, WhatsApp, O365, and future platforms. It normalizes, filters, and compactly formats messages so downstream agents spend hundreds of tokens instead of thousands per interaction.

**ts4k does not analyze or act on messages.** It retrieves, normalizes, and delivers. All intelligence belongs to the consuming agent.

## Project Plan

Read `docs/intentions.md` for the stable vision — what ts4k aims to be, independent of implementation status. This is the canonical reference for capabilities, anti-goals, design rules, and success criteria.

Read `docs/rip/ts4k-rip.md` for the RIP (Rapid Implementation Plan) — the consolidated architecture, design principles, prototype strategy, and phased implementation roadmap. Phase working docs are in `docs/rip/phase-*.md`. The original design doc is `docs/plan-v1.md`.

## Architecture at a Glance

- **Adapters** wrap platform APIs (direct Google API for Gmail, Microsoft Graph for O365, WhatsApp MCP bridge). ts4k does not reimplement platform protocols.
- **Normalize → Filter → Format** pipeline processes every message before output.
- **Output formats**: pipe-delimited for listings, mini XML for message bodies. Optimized for LLM token efficiency.
- **State**: JSON files (watermarks, contact identity map, filter config). Git-friendly, human-readable.
- **Three modes**: CLI (`ts4k whatsnew`), MCP server, and Skill (thin Claude Code stub calling `ts4k skill`).

## Tech Stack

- Python (3.12+ per ADR-1)
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
7. **UTC internally, local at display.** Adapters emit UTC timestamps; only `core/format.py` converts, to one global display timezone (`ts4k.core.tz.display_tzinfo`). All-day calendar events stay bare dates.

## Implementation Phases

0. **Phase 0**: Prototype validation — Normalizer, MCP bridge, full loop, bulk probe, container
1. **Phase 1**: Gmail MVP — Core pipeline, Gmail adapter, CLI (wn/g/t/l/h/skill), watermarks, output formats
2. **Phase 2**: WhatsApp + Contacts — WhatsApp adapter, parallel calls, contacts, status, stats, filters
3. **Phase 3**: O365 + MCP Server — O365 adapter, MCP server mode, 3-platform whatsnew
4. **Phase 4**: Overview + Bulk — Overview drill-down, background tasks, cache, resumable batch
5. **Phase 5**: Send + Docker + Release — Send/draft, Docker template, PyPI, docs, public release

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
│   │   ├── gmail.py         # Gmail adapter (direct Google API)
│   │   ├── o365.py          # O365 adapter (direct Microsoft Graph API)
│   │   └── whatsapp.py      # WhatsApp adapter (MCP bridge)
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

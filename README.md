# ts4k — Token Saver 4000

Unified messaging gateway that gives LLM agents token-efficient access to messages across platforms (Gmail, WhatsApp, Telegram, and future sources).

ts4k sits between raw messaging APIs and AI consumers, solving two problems: **platform fragmentation** and **token waste**. It retrieves, normalizes, and delivers messages in formats optimized for LLM context windows.

## What It Does

An agent calls `ts4k whatsnew` and gets a unified, cross-platform feed of recent messages — normalized, deduplicated, filtered, and formatted — in a single tool call costing hundreds of tokens instead of tens of thousands.

```
SOURCE | CONTACT | THREAD_ID | MSG_COUNT | LATEST | SIZE
gmail | alice@acme.com | g_thr_abc | 3 | 2026-02-20T09:15:00Z | 2kb
whatsapp | +31612345678 | w_chat_alice | 5 | 2026-02-20T09:30:00Z | 1kb
telegram | @bob_t | t_chat_bob | 1 | 2026-02-20T08:50:00Z | 500b
```

Raw HTML email: ~8,000 tokens. After ts4k normalization: ~400 tokens. **20x reduction.**

## Modes

- **CLI** — `ts4k whatsnew`, `ts4k read <id>`, `ts4k history <contact>`, etc.
- **MCP Server** — exposes the same commands as MCP tools for Claude Code or any MCP-compatible agent.

## Architecture

ts4k wraps existing platform adapters (MCP servers, CLIs, APIs) rather than reimplementing messaging integrations. Each platform gets an adapter; platform failures are isolated.

```
ts4k (normalize → filter → format)
  ├── Gmail Adapter  → gog CLI
  ├── WhatsApp Adapter → WA MCP
  ├── Telegram Adapter → TG API
  └── Future adapters
```

## Key Principles

- **Metadata first, content on demand** — never return more than the agent asked for
- **No LLM calls inside ts4k** — it's the tool the LLM calls, not a tool that calls LLMs
- **Adapter-agnostic output** — a WhatsApp message and a Gmail message look identical after normalization
- **Fail gracefully** — one adapter down doesn't block the others

## Tech Stack

Python, official MCP SDK, html2text/beautifulsoup4, JSON state files.

## Status

Early development. See [docs/plan-v1.md](docs/plan-v1.md) for the full project plan and phased implementation roadmap.

## License

MIT

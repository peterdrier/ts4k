# ts4k — Open Work
Last synced: 2026-03-05T12:00

## P1 — Bugs / Reliability

- [ ] **Deploy plan drift** — docs/deploy-plan.md describes bundled Docker/s6/connectors architecture not present in repo. Sync docs to reality or build what's described. (codex-issues #3)
- [ ] **Secrets handling unresolved** — .env/mounted secrets/wizard undecided while connector creds are central. Security gap in deployment path. (codex-issues #4)
- [x] ~~**CLI help cleanup**~~ [#4](https://github.com/peterdrier/ts4k/issues/4) — `-h` deduplicated with `aliases=`; cross-references `help`; auth errors link to setup docs; first-run `help` shows quick setup guide.
- [x] ~~**src add breaks WhatsApp**~~ [#6](https://github.com/peterdrier/ts4k/issues/6) — `server_command` auto-split on spaces in CLI; coerced from string in adapter; help updated with examples and o365 keys.
- [ ] **Usage log: remaining items** [#10](https://github.com/peterdrier/ts4k/issues/10) — WhatsApp raw JIDs instead of contact names; stderr noise forcing `2>&1`; filter flag underused. *(5 of 10 items fixed: relative `--since`, empty results, `0b` size, non-interactive auth, wrong syntax errors.)*

## P2 — Token Optimization / UX (GitHub Issues)

- [ ] **Trim MCP surface** [#2](https://github.com/peterdrier/ts4k/issues/2) — Collapse lesser-used MCP functions into single call routed through CLI parser. Track call frequency to inform decisions. Balance savings vs LLM usability.
- [x] ~~**LLM-oriented help mode**~~ [#8](https://github.com/peterdrier/ts4k/issues/8) — `ts4k help --llm` and `ts4k skill setup`: structured agent reference with context-aware output, setup sequences per provider, error→fix mappings.
- [ ] **Mailbox stats** [#9](https://github.com/peterdrier/ts4k/issues/9) — Label/folder counts for inbox-zero tracking. Gmail `labels.list()` API is efficient. Enhance `status` or `overview` with live inbox/unread/category counts.

## P3 — Architecture / Quality

- [ ] **MCP lifecycle ownership** — Unclear who owns restart/failure semantics. Resolve before production. (codex-issues #6)
- [ ] **Observability plan** — No concrete logging/metrics/alerting/tracing plan beyond status output. (codex-issues #7)
- [ ] **Bundle whatsapp-mcp-server** [#7](https://github.com/peterdrier/ts4k/issues/7) — Single-command WhatsApp setup: clone, install deps, generate config, register source, guide auth. Managed service directory at `~/.config/ts4k/services/`. Phase 2+ work.

## Phase 5 — Send + Docker + Release (incomplete)

- [ ] **Send/draft commands** — Safety-railed send and draft-only mode across all adapters. Needs adapter send methods, CLI/MCP commands, confirmation flags.
- [ ] **Docker template** — Dockerfile + docker-compose for ts4k + connectors. deploy-plan.md has full design; no implementation yet. *Soft-blocked by secrets handling decision (P1).*
- [x] ~~PyPI packaging~~ — `pyproject.toml` with entry points, installable via `pip install -e .`. Minor metadata polish remaining.
- [x] ~~Public release docs~~ — README, setup guides, usage docs (73543c3). Docker/contributing docs pending. *(Setup doc improvements tracked in [#5](https://github.com/peterdrier/ts4k/issues/5))*
- [x] ~~Help system~~ — Human-readable `help` + LLM-optimized `skill` reference, both working. *(Improvements tracked in [#4](https://github.com/peterdrier/ts4k/issues/4))*

## Phase 6 — Calendar (not started)

- [ ] **Google Calendar adapter** — Wrap `@cocal/google-calendar-mcp`. New `src/ts4k/adapters/gcal.py`. (Phase 6a)
- [ ] **O365 Calendar extension** — Add calendar tool calls to existing O365 adapter (same Graph auth). (Phase 6b)
- [ ] **`ts4k cal` command** — `today`, `week`, `next`, `range`, `event` subcommands. Unified view across calendar sources. (Phase 6a-b)
- [ ] **Attendee context enrichment** — Cross-reference calendar attendees with contacts map + message cache. The headline feature: "You have 3 unread from Sarah, last thread: Q1 budget review." (Phase 6c) *Depends on at least one calendar adapter (6a or 6b).*

## P4 — Future Adapters

- [ ] Slack adapter (`slack_sdk`)
- [ ] Teams Chat adapter (reuse Graph auth)
- [ ] Telegram adapter (`python-telegram-bot` or `Telethon`)

## P5 — Stretch / Nice-to-Have

- [ ] Bundled OAuth credentials for easier onboarding
- [ ] Discord adapter
- [ ] GitHub Notifications adapter

## Recently Completed

- [x] **Optimize token usage** [#1](https://github.com/peterdrier/ts4k/issues/1) — Measured actual token counts, trimmed timestamps/IDs. (closed)
- [x] **Local project config** [#3](https://github.com/peterdrier/ts4k/issues/3) — Resolution order: env var → `.ts4k/` in cwd → `~/.config/ts4k/`. `--local` flag added. (closed)
- [x] **Atomic state writes** — Fixed state corruption risk under concurrent use (666f3e3)
- [x] **Batch cache optimization** — Reduced O(n^2) cache rewrite overhead (666f3e3)
- [x] **Preload cancel fix** — `--cancel` now actually stops spawned processes (666f3e3)
- [x] **Disk check hardening** — Robust error handling for inaccessible paths (3ac6a98)
- [x] **CI workflow added** — Automated test gating now in place (3ac6a98)
- [x] **License fixed to MIT** — README and pyproject.toml now consistent (3ac6a98)
- [x] **Direct Gmail API** — Replaced MCP bridge with Google API calls (fe6420f)
- [x] **Direct O365 API** — Replaced MCP bridge with Microsoft Graph calls (74ffaeb)
- [x] **Public release docs** — README, contributing guide, etc. (73543c3)

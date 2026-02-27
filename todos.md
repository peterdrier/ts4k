# ts4k — Open Work
Last synced: 2026-02-25T16:00

## P1 — Bugs / Reliability

- [ ] **Deploy plan drift** — docs/deploy-plan.md describes bundled Docker/s6/connectors architecture not present in repo. Sync docs to reality or build what's described. (codex-issues #3)
- [ ] **Secrets handling unresolved** — .env/mounted secrets/wizard undecided while connector creds are central. Security gap in deployment path. (codex-issues #4)
- [ ] **CLI help needs work** [#4](https://github.com/peterdrier/ts4k/issues/4) — `-h` lists aliases as separate commands; `help` crashes with no sources (cfg.path bug); auth errors don't link to setup docs; first-run UX is poor.

## P2 — Token Optimization (GitHub Issues)

- [ ] **Optimize token usage** [#1](https://github.com/peterdrier/ts4k/issues/1) — Use Anthropic Python API to measure actual in/out token counts. Trim timestamps and message IDs where possible. Cache slim versions.
- [ ] **Trim MCP surface** [#2](https://github.com/peterdrier/ts4k/issues/2) — Collapse lesser-used MCP functions into single call routed through CLI parser. Track call frequency to inform decisions. Balance savings vs LLM usability.

## P3 — Architecture / Quality

- [ ] **Local project config with global fallback** [#3](https://github.com/peterdrier/ts4k/issues/3) — Resolution order: env var → `.ts4k/` in cwd → `~/.config/ts4k/`. Add `--local` flag to config commands. Replace per-module `_CONFIG_DIR` with shared resolver.
- [ ] **MCP lifecycle ownership** — Unclear who owns restart/failure semantics. Resolve before production. (codex-issues #6)
- [ ] **Observability plan** — No concrete logging/metrics/alerting/tracing plan beyond status output. (codex-issues #7)

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

- [x] **Atomic state writes** — Fixed state corruption risk under concurrent use (666f3e3)
- [x] **Batch cache optimization** — Reduced O(n^2) cache rewrite overhead (666f3e3)
- [x] **Preload cancel fix** — `--cancel` now actually stops spawned processes (666f3e3)
- [x] **Disk check hardening** — Robust error handling for inaccessible paths (3ac6a98)
- [x] **CI workflow added** — Automated test gating now in place (3ac6a98)
- [x] **License fixed to MIT** — README and pyproject.toml now consistent (3ac6a98)
- [x] **Direct Gmail API** — Replaced MCP bridge with Google API calls (fe6420f)
- [x] **Direct O365 API** — Replaced MCP bridge with Microsoft Graph calls (74ffaeb)
- [x] **Public release docs** — README, contributing guide, etc. (73543c3)

# ts4k — Open Work
Last synced: 2026-03-22T09:00 (v0.1.20)

## P1 — Bugs / Reliability

- [ ] **whatsnew watermark skips unread messages** [#23](https://github.com/peterdrier/ts4k/issues/23) — When `count < total new`, watermark advances past all messages but only returns `count`. Permanently skips the rest.
- [ ] **O365 bodyPreview cached as full body** [#24](https://github.com/peterdrier/ts4k/issues/24) — O365 adapter stores `bodyPreview` as `body`; cache treats it as full content. `get` returns truncated snippet instead of real body.
- [ ] **count applied before filters under-fills results** [#25](https://github.com/peterdrier/ts4k/issues/25) — Sort+slice to `count` happens before `apply_filters()`. Filtered messages consume result slots, returning fewer actionable messages than requested.
- [x] ~~**`status` reports false [ok] for expired tokens**~~ [#21](https://github.com/peterdrier/ts4k/issues/21) — Fixed: unified `ts4k auth [target]` command, token health validation with scope checking, `[ok]`/`[auth]`/`[error]`/`[na]` tags in status output. (v0.1.18)
- [x] ~~**Deploy plan drift**~~ — deploy-plan.md updated: connector table reflects direct APIs for Gmail/O365, submodule scope reduced, s6 justification revised. Intentions doc created at `docs/intentions.md`. (codex-issues #3)
- [ ] **Secrets handling unresolved** — .env/mounted secrets/wizard undecided while connector creds are central. Security gap in deployment path. (codex-issues #4)
- [x] ~~**CLI help cleanup**~~ [#4](https://github.com/peterdrier/ts4k/issues/4) — `-h` deduplicated with `aliases=`; cross-references `help`; auth errors link to setup docs; first-run `help` shows quick setup guide.
- [x] ~~**src add breaks WhatsApp**~~ [#6](https://github.com/peterdrier/ts4k/issues/6) — `server_command` auto-split on spaces in CLI; coerced from string in adapter; help updated with examples and o365 keys.
- [x] ~~**Usage log improvements**~~ [#10](https://github.com/peterdrier/ts4k/issues/10) — All 10 items addressed: relative `--since`, empty results, `0b` size, non-interactive auth, wrong syntax errors, WhatsApp JID→name fallback, stderr→stdout for command results, thread from message ID via cache, common mistakes in help, filter discoverability via skill/llm-help.

## P1.5 — Bugs / Normalization

- [ ] **WhatsApp 1-on-1 chat: wrong `from` field** — Private chats show an unrelated label (e.g. "Anker technology") in `from` instead of the contact name. Sender ends up in `subject`. Likely a WhatsApp adapter normalization bug — group vs 1-on-1 detection issue.
- [ ] **WhatsApp group system notifications swap sender/chat** [#11](https://github.com/peterdrier/ts4k/issues/11) — "Group members have changed" notifications show group name in FROM and member in SUBJECT (reversed). Either filter out membership-change notifications or fix the mapping.
- [x] ~~**Gmail 429 on listings**~~ [#12](https://github.com/peterdrier/ts4k/issues/12) — Fixed: chunked batch fetch (groups of 25), cache check before API calls, 429 retry.
- [ ] **O365 search returns 400 + no sender filtering** [#15](https://github.com/peterdrier/ts4k/issues/15) — `list -q` and `preload --query` fail with 400 on O365. `updates` caps at 200 with no pagination. No `--from`/`--domain` filter. `overview` can't drill down to message listings. Skill output doesn't guide agents around these gaps.

## P2 — Token Optimization / UX (GitHub Issues)

- [x] ~~**Skill text: unified whatsnew guidance**~~ [#14](https://github.com/peterdrier/ts4k/issues/14) — Fixed: whatsnew shows all-sources-by-default, `--source` as optional filter.
- [x] ~~**Skill text: prevent piped workarounds**~~ [#13](https://github.com/peterdrier/ts4k/issues/13) — Fixed: anti-piping guidance with correct alternatives.
- [ ] **Thread-level operations mode** [#20](https://github.com/peterdrier/ts4k/issues/20) — Gmail thread-based manage+listings. `manage archive <ref> --thread` archives entire thread. Thread-collapsed listings (one row per thread with participants, count, date range). Major token savings for multi-message threads. O365 equivalent TBD.
- [ ] **Skill output: agents check --help too often** [#16](https://github.com/peterdrier/ts4k/issues/16) — Agents call `--help` 4x/session. Skill output needs compact cheat sheet with `get` syntax examples. Clarify argument order.
- [ ] **Document known-empty/noisy inboxes** [#17](https://github.com/peterdrier/ts4k/issues/17) — Agents waste calls widening date ranges on empty/low-traffic inboxes. Add inbox metadata to `skill` output or `ts4k sources` command showing activity level, noise patterns, recommended date ranges.
- [ ] **Setup doc deep links** [#5](https://github.com/peterdrier/ts4k/issues/5) — Add direct console/portal URLs to Gmail/O365 setup docs so users don't have to navigate by menu path. Gmail needs 4 deep links; O365 sidebar hints. Partially addressed by O365 doc improvements (c948c36, ea07f3d).

## P3 — Architecture / Quality

- [ ] **MCP lifecycle ownership** — Unclear who owns restart/failure semantics. Resolve before production. (codex-issues #6)
- [ ] **Observability plan** — No concrete logging/metrics/alerting/tracing plan beyond status output. (codex-issues #7)
- [ ] **Bundle whatsapp-mcp-server** [#7](https://github.com/peterdrier/ts4k/issues/7) — Single-command WhatsApp setup: clone, install deps, generate config, register source, guide auth. Managed service directory at `~/.config/ts4k/services/`. Phase 2+ work.
- [ ] [MEDIUM/Security] **OAuth token files lack restrictive permissions** — Token files written without `0o600`; readable by other users on shared systems. `auth/google.py:90,138`, `auth/microsoft.py:137`. (found by: security audit, 2026-03-08)
- [ ] [MEDIUM/Security] **O365 input validation** — Message IDs and mailbox not validated before URL interpolation; path traversal and OData injection possible. `adapters/o365.py:205,305,319`. (found by: security audit, 2026-03-08)
- [ ] [MEDIUM/Security] **MCP HTTP transport has no auth** — Anyone who can reach the port gets full tool access. `server.py:333-336`. (found by: security audit, 2026-03-08)
- [ ] [MEDIUM/Quality] **`commands.py` decomposition** — 1578 lines; Gemini identified 7 extractable module groups + 2 consolidation patterns. (found by: complexity scan, 2026-03-08)
- [ ] [LOW/Security] Cache body filename sanitization only replaces `:` → `_`; crafted IDs could write outside cache dir. `state/cache.py:69`. (found by: security audit, 2026-03-08)
- [ ] [LOW/Infra] `.pytest_tmp` with broken NTFS permissions — blocks Gemini CLI folder scanning. Remove via `cmd /c rmdir .pytest_tmp`. (found by: maintenance scan, 2026-03-08)

## Phase 5 — Send + Docker + Release (incomplete)

- [x] ~~**Draft message creation**~~ [#18](https://github.com/peterdrier/ts4k/issues/18) — Gmail and O365 draft creation with reply threading, blockquoting, and proper headers. CLI `draft create`, MCP `draft` tool. Requires source level >= draft.
- [x] ~~**Non-destructive mailbox management**~~ [#19](https://github.com/peterdrier/ts4k/issues/19) — Archive, label, mark read/unread, trash for Gmail and O365. Batch support, dry-run mode, ref resolution. CLI `manage`/`m`, MCP `manage` tool. Requires source level >= modify. Per-source permission levels (readonly/modify/draft/send) with scope-aware OAuth re-auth.
- [ ] **Docker template** — Dockerfile + docker-compose for ts4k + connectors. deploy-plan.md has full design; no implementation yet. *Soft-blocked by secrets handling decision (P1).*
- [x] ~~PyPI packaging~~ — `pyproject.toml` with entry points, installable via `pip install -e .`. Minor metadata polish remaining.
- [x] ~~Public release docs~~ — README, setup guides, usage docs (73543c3). Docker/contributing docs pending. *(Setup doc improvements tracked in [#5](https://github.com/peterdrier/ts4k/issues/5))*
- [x] ~~Help system~~ — Human-readable `help` + LLM-optimized `skill` reference, both working. *(Improvements tracked in [#4](https://github.com/peterdrier/ts4k/issues/4))*

## Phase 6 — Calendar

- [x] ~~**Google Calendar adapter**~~ — Direct Google Calendar API via `src/ts4k/adapters/gcal.py`. List/create/update events, RSVP, recurring event support, adaptive time formatting. (Phase 6a, v0.1.17)
- [x] ~~**`ts4k cal` command**~~ — `today`, `week`, `next`, `range`, `event`, `create`, `update`, `rsvp` subcommands with setup wizard. MCP tools: `cal`, `cal_create`, `cal_manage`. (Phase 6a, v0.1.17)
- [x] ~~**O365 Calendar extension**~~ — O365CalAdapter with list/create/update events, setup wizard, `list_o365_calendars`. (Phase 6b, 873f527)
- [ ] **Attendee context enrichment** — Cross-reference calendar attendees with contacts map + message cache. The headline feature: "You have 3 unread from Sarah, last thread: Q1 budget review." (Phase 6c) *Depends on at least one calendar adapter (6a or 6b).*

## P4 — Future Adapters

- [ ] **GitHub adapter** [#22](https://github.com/peterdrier/ts4k/issues/22) — Notifications, issues, PRs via GitHub REST API. Category-based filtering (ci, review, mention, dependabot). Source prefix `gh:`, PAT auth.
- [ ] Slack adapter (`slack_sdk`)
- [ ] Teams Chat adapter (reuse Graph auth)
- [ ] Telegram adapter (`python-telegram-bot` or `Telethon`)

## P5 — Stretch / Nice-to-Have

- [ ] Bundled OAuth credentials for easier onboarding
- [ ] Discord adapter

## Recently Completed

- [x] **CLI `--version` flag** — `ts4k --version` now shows version from package metadata; removed stale hardcoded `__version__` in `__init__.py`.
- [x] **Trim MCP surface** [#2](https://github.com/peterdrier/ts4k/issues/2) — 11→7 tools, 29% token savings. (closed)
- [x] **LLM-oriented help mode** [#8](https://github.com/peterdrier/ts4k/issues/8) — `ts4k help --llm` + `ts4k skill setup`. (closed)
- [x] **Mailbox stats** [#9](https://github.com/peterdrier/ts4k/issues/9) — `ts4k status --live` with label/folder counts. (closed)
- [x] **Usage log improvements** [#10](https://github.com/peterdrier/ts4k/issues/10) — All 10 friction points addressed. (closed)
- [x] **CLI help cleanup** [#4](https://github.com/peterdrier/ts4k/issues/4) — Deduplicated aliases, fixed crash, added setup links. (closed)
- [x] **src add fixes** [#6](https://github.com/peterdrier/ts4k/issues/6) — server_command auto-split, coercion, help updates. (closed)
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
- [x] **Intentions doc + deploy plan refresh** — Created `docs/intentions.md` as stable vision doc. Updated deploy-plan.md for direct API reality. (4b3769a)
- [x] **Code review fixes** — O365 token filename mismatch, CLAUDE.md repo layout/platform list, base.py docstring, WhatsApp architecture in deploy-plan.
- [x] **Google Calendar adapter (Phase 6a)** [v0.1.17] — GcalAdapter with list/create/update/RSVP, `cal` CLI subcommand with setup wizard, 3 MCP tools, help/skill docs updated. (15 commits, 24e6487)
- [x] **O365 Calendar adapter (Phase 6b)** — O365CalAdapter wired into calendar commands, setup wizard, `list_o365_calendars` command. (873f527)
- [x] **Draft message creation** [#18](https://github.com/peterdrier/ts4k/issues/18) — Gmail + O365 draft creation with reply threading. (closed)
- [x] **Non-destructive mailbox management** [#19](https://github.com/peterdrier/ts4k/issues/19) — Archive, label, read/unread, trash for Gmail + O365. (closed)

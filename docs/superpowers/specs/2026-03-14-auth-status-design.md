# Unified Auth Command & Token Health Validation

**Date:** 2026-03-14
**Issue:** [#21](https://github.com/peterdrier/ts4k/issues/21) — status should validate token health, not just config existence

## Problem

1. `ts4k status` reports `[ok]` for sources whose config exists but whose OAuth token is expired/invalid. Agents gate on status before pulling messages, so false-green causes silent mid-scan failures.
2. `ts4k auth gmail <email>` / `ts4k auth o365 <prefix>` is clunky. Error messages suggest `ts4k auth g` which doesn't work because `g` isn't a subcommand.
3. After successful auth, scopes are shown but not token expiry.
4. No global auth check — you have to check each source individually.

## Design

### 1. Unified `ts4k auth [target]`

Replace the current `ts4k auth gmail <email>` / `ts4k auth o365 [source]` subcommand structure with a single positional argument.

**CLI signature:**

```
ts4k auth [target] [--check] [--no-calendar]
```

**Resolution logic for `target`:**

1. Look up `target` as a source prefix in `sources.json` — found? Use that source's provider + config.
2. Look up `target` as a provider name (`gmail`, `o365`) — found? Auth all sources of that provider.
3. `target` omitted + `--check` — validate all sources, no re-auth.
4. `target` omitted + no `--check` — print usage/help. Don't silently re-auth everything.

**Examples:**

| Command | Behavior |
|---------|----------|
| `ts4k auth g` | Auth source `g` (Gmail, resolves email from config) |
| `ts4k auth gmail` | Auth all Gmail sources |
| `ts4k auth o` | Auth source `o` (O365) |
| `ts4k auth o365` | Auth all O365 sources |
| `ts4k auth --check` | Validate all sources, no re-auth |
| `ts4k auth g --check` | Validate just source `g` |
| `ts4k auth gmail --check` | Validate all Gmail sources |

**Help text (`ts4k auth --help`):**

```
Authenticate with a messaging platform or validate existing tokens.

Usage:
  ts4k auth [target] [--check] [--no-calendar]

Target resolution:
  1. Source prefix first (g, gn, o, oc) — auths that specific source
  2. Provider name (gmail, o365) — auths all sources of that provider
  3. Omitted + --check — validates all sources
  4. Omitted without --check — shows this help

Examples:
  ts4k auth g                  Auth source 'g' (resolves email from config)
  ts4k auth gmail              Auth all Gmail sources
  ts4k auth o                  Auth source 'o' (O365, device code flow)
  ts4k auth --check            Validate all sources, no re-auth
  ts4k auth g --check          Validate just source 'g'

Options:
  --check         Validate tokens without re-auth
  --no-calendar   Skip requesting calendar scopes
```

**Setup flow preserved:** `ts4k src add g gmail email=alice@gmail.com` comes first, then `ts4k auth g` replaces `ts4k auth gmail alice@gmail.com`. The email is already in source config.

### 2. Token Health Validation

**New function: `check_token_health(prefix, cfg) -> TokenHealth`**

Located in a new module or in `commands.py`. Returns a dataclass/namedtuple:

```python
@dataclass
class TokenHealth:
    status: str       # "ok", "auth", "error", "na"
    expiry: datetime | None
    scopes: list[str]
    detail: str       # human-readable status line
```

**Google tokens:**

- Load `token.json`, parse with `Credentials.from_authorized_user_file()`
- `creds.valid` → `ok`, expiry from `creds.expiry`
- `creds.expired and creds.refresh_token` → attempt `creds.refresh(Request())`. Success → `ok` (refreshed). Fail → `auth`
- No token file → `auth`
- No `client_secret.json` → `na`

**Microsoft tokens:**

- Load MSAL cache, call `acquire_token_silent()` (lightweight — local cache if fresh, refresh grant if expired)
- `access_token` in result → `ok`. Parse `expires_in` to compute expiry
- Silent acquisition fails → `auth`
- No cache file → `auth`
- No `client_id` → `na`

**WhatsApp:** Skip (session-based, no token to validate)

**Calendar sources (gcal, o365cal):** Validate the same way as their mail counterpart — same token file, same auth mechanism. A gcal source can exist without a gmail source, so each is checked independently.

### 3. Status Command Integration

**Current:** `_sources_needing_auth()` checks token file existence. Shows `[ok]` or `[not found]`.

**New:** Status calls `check_token_health()` per source:

```
Sources:
  g   gmail   peter.drier@gmail.com     [ok] expires 2026-03-15 09:30
  gn  gmail   peter@company.com         [auth] token expired — ts4k auth gn
  o   o365    peter@company.com         [ok] expires 2026-03-14 18:45
  w   whatsapp                          [ok]
  gc  gcal    peter.drier@gmail.com     [ok] expires 2026-03-15 09:30
```

Status tags:
- `[ok]` + expiry date
- `[auth]` + remediation command (`ts4k auth <prefix>`)
- `[error]` + brief reason
- `[n/a]` for unconfigured

**`_sources_needing_auth()` refactored** to use `check_token_health()`. Fixes LLM help output too since it calls the same function.

**Performance:** Google refresh is ~200ms network call. MSAL silent is local cache unless refresh needed. Status stays fast for healthy tokens. Sources needing browser auth get caught as `[auth]` without hanging.

### 4. Auth Command Output

**After successful auth (no `--check`):**

```
Authenticated g (peter.drier@gmail.com) successfully.
Scopes: gmail.readonly, calendar.readonly
Expires: 2026-03-15 09:30
```

**After `--check` (all sources):**

```
g   gmail   peter.drier@gmail.com     [ok] expires 2026-03-15 09:30
o   o365    peter@company.com         [ok] expires 2026-03-14 18:45
gc  gcal    peter.drier@gmail.com     [ok] expires 2026-03-15 09:30
oc  o365cal peter@company.com         [ok] expires 2026-03-14 18:45
w   whatsapp                          [ok]
```

Same format as status sources section — consistent, scannable. Exit code 1 if any source shows `[auth]` or `[error]`.

**After `--check` with a target:**

```
g   gmail   peter.drier@gmail.com     [ok] expires 2026-03-15 09:30
```

Single line, same format.

### 5. Error Message Updates

All existing messages that reference `ts4k auth gmail <email>` or `ts4k auth o365` get updated to `ts4k auth <prefix>`.

**Locations:**

| File | Line(s) | Current | New |
|------|---------|---------|-----|
| `auth/google.py` | 133 | `run 'ts4k auth g' in a terminal` | Already correct (lucky) |
| `commands.py` | 1872 | `ts4k auth gmail {cfg.get('email', '<email>')}` | `ts4k auth {prefix}` |
| `commands.py` | 1874 | `ts4k auth o365 {prefix}` | `ts4k auth {prefix}` |
| `commands.py` | 1955 | `ts4k auth gmail <email>` | `ts4k auth <prefix>` |
| `commands.py` | 1961 | `ts4k auth o365` | `ts4k auth <prefix>` |
| `commands.py` | 1980 | `ts4k auth gmail <email>` | `ts4k auth <prefix>` |
| `cli.py` | 203 | `auth gmail\|o365` | `auth [source\|provider]` |
| `cli.py` | 222-224 | `ts4k auth gmail you@gmail.com` | `ts4k auth g` |
| `cli.py` | 929 | `ts4k auth gmail {email}` | `ts4k auth {prefix}` (needs prefix in scope) |
| `docs/setup-gmail.md` | 74,90,etc. | `ts4k auth gmail alice@gmail.com` | `ts4k auth g` |
| `docs/setup-o365.md` | 100,126,etc. | `ts4k auth o365` | `ts4k auth o` |
| `docs/usage.md` | 171-179 | Old syntax | New syntax |

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/ts4k/cli.py` | Modify | Replace auth subparser with unified positional arg; rewrite `_cmd_auth()` |
| `src/ts4k/commands.py` | Modify | Add `check_token_health()`, refactor `_sources_needing_auth()`, update status output, update error messages |
| `src/ts4k/auth/google.py` | Modify | Add `validate_token()` function (check without triggering browser flow) |
| `src/ts4k/auth/microsoft.py` | Modify | Add `validate_token()` function (silent check only) |
| `docs/setup-gmail.md` | Modify | Update auth command syntax |
| `docs/setup-o365.md` | Modify | Update auth command syntax |
| `docs/usage.md` | Modify | Update auth command syntax |
| `tests/test_auth_check.py` | Create | Tests for token health validation and unified auth resolution |

## Non-Goals

- WhatsApp token validation (session-based, no OAuth token)
- Automatic re-auth from status (status reports, doesn't fix)
- Token refresh scheduling or background refresh

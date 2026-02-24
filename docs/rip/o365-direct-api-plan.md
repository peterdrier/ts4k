# O365 Adapter: Direct Microsoft Graph API Rewrite

## Context

ts4k's O365 adapter currently calls a TypeScript MCP server (`@softeria/ms-365-mcp-server`) over MCP stdio protocol. That server itself calls Microsoft Graph API. The data path is: Python → MCP protocol → Node.js subprocess → Graph API → JSON → re-parse. This plan replaces the MCP middleman with direct `msgraph-sdk-python` (or raw `httpx` + MSAL) calls, mirroring the Gmail adapter rewrite completed in commit [TBD].

## Reference Implementation

The Gmail rewrite is the template. Study these files before starting:

- `src/ts4k/auth/google.py` — OAuth credential management pattern
- `src/ts4k/adapters/gmail.py` — direct API adapter with pure converters
- `src/ts4k/commands.py` — simplified `_make_adapter()`, no provider-specific branches
- `tests/test_gmail_adapter.py` — Gmail API JSON fixtures, mock service pattern
- `tests/test_google_auth.py` — auth module tests with tmp_path fixtures

## Current O365 Architecture (What to Replace)

### Adapter (`src/ts4k/adapters/o365.py`)
- **Connection:** MCP stdio client → spawns `npx -y @softeria/ms-365-mcp-server` subprocess
- **Config:** `O365AdapterConfig` with `server_command`, `server_args`, `server_cwd`, `server_env`, `mailbox`
- **Tool routing:** Personal mailbox uses `list-mail-messages` / `get-mail-message`; shared mailbox uses `list-shared-mailbox-messages` / `get-shared-mailbox-message` with `userId` arg
- **Parsers:** `parse_list_response(text)` and `parse_message_response(text)` — these parse JSON that the MCP server returns (Graph API JSON wrapped in MCP text content)
- **Thread fetch:** Uses `conversationId` filter on list — no N+1 problem (keep this pattern)
- **Discover:** `discover_mailboxes()` calls `get-user-profile` tool

### Commands integration (`src/ts4k/commands.py`)
- `_make_adapter()` O365 branch (lines ~90-122): builds config from `server_command`, `client_id`, `tenant_id` env vars
- No O365-specific N+1 workaround — O365 list already returns headers (unlike old Gmail)
- Provider aliases: `"outlook"`, `"office"`, `"365"` → `"o365"`

### Tests (`tests/test_o365_adapter.py`)
- Mock MCP `ClientSession` with `AsyncMock`
- Fixtures: `LIST_RESPONSE_JSON`, `MESSAGE_RESPONSE_JSON`, `THREAD_MESSAGES_JSON`, `DISCOVER_RESPONSE_JSON`
- 50+ tests across 11 test classes

### Pipeline tests (`tests/test_pipeline.py`)
- Currently only uses Gmail fixtures — no O365-specific pipeline tests to update

## Implementation Steps

### Step 1: Add Microsoft auth module

**New file:** `src/ts4k/auth/microsoft.py`

Provides:
- `get_credentials(client_id, tenant_id, scopes, config_dir)` — loads or creates MSAL credentials
- `build_graph_client(client_id, tenant_id, config_dir)` — returns an authenticated `httpx.AsyncClient` with token middleware, OR a `msgraph` `GraphServiceClient`

**Decision needed:** Choose between:
- **Option A: `msgraph-sdk-python`** — Microsoft's official SDK. Typed, auto-generated from OpenAPI. Heavy dependency tree (azure-identity, kiota, etc). May be overkill.
- **Option B: Raw `httpx` + `msal`** — Lighter. Use MSAL for auth, httpx for Graph API calls. More control, fewer dependencies. Matches the simplicity of the Gmail adapter (which uses google-api-python-client directly).
- **Recommendation:** Option B (`httpx` + `msal`). The Graph API calls ts4k needs are simple REST (list messages, get message, get thread via filter). No need for the full SDK.

**Auth flow:** Microsoft uses MSAL (Microsoft Authentication Library):
- Device code flow (headless-friendly, unlike Gmail's browser redirect)
- Or interactive browser flow via `PublicClientApplication`
- Token cache: MSAL's `SerializableTokenCache` persisted to `~/.config/ts4k/microsoft/<client_id>/token_cache.json`

**Config needed from user:**
- `client_id` — Azure AD app registration client ID
- `tenant_id` — Azure AD tenant ID (or `"common"` for multi-tenant)
- These already exist in `sources.json` as `client_id` and `tenant_id` fields

**Scopes:** `["https://graph.microsoft.com/Mail.Read"]` initially. Phase 5 adds `Mail.Send`.

**New CLI command:** `ts4k auth o365 --client-id <id> --tenant-id <id>` — triggers the device code flow explicitly.

### Step 2: Rewrite response converters

The current parsers already handle JSON (Graph API format), but they parse it from MCP text content. Replace with converters that take Graph API response dicts directly.

**Delete from `o365.py`:** `parse_list_response()`, `parse_message_response()`, all MCP-related imports.

**New helpers in `o365.py`:**

| Function | Input | Output | Notes |
|----------|-------|--------|-------|
| `_format_email_address(ea)` | Graph `emailAddress` dict | str | Keep existing — `"Name <addr>"` |
| `_format_from(msg)` | Graph message dict | str | Keep existing |
| `_format_recipients(recipients)` | Graph recipients list | str | Keep existing |
| `_msg_to_headers(msg, prefix)` | Graph message dict | dict | For listings: id, thread_id, from, subject, date, snippet, source |
| `_msg_to_full(msg, prefix)` | Graph message dict | dict | For read: adds body, to, cc, message_id, attachments |
| `_thread_to_dict(messages, prefix)` | list of Graph message dicts | dict | thread_id, subject, message_count, messages list |

**Key fields from Graph API:**
- `id` — message ID
- `conversationId` — thread ID
- `subject`, `from.emailAddress`, `receivedDateTime`
- `bodyPreview` — snippet (~255 chars, free on every message)
- `body.content` — full body (HTML or text, controlled by `body.contentType`)
- `toRecipients`, `ccRecipients` — structured recipient lists
- `internetMessageId` — RFC-822 Message-ID
- `hasAttachments`, then separate `/attachments` endpoint for details

### Step 3: Rewrite O365Adapter class

**`O365AdapterConfig`** — simplified:
```python
@dataclass
class O365AdapterConfig:
    client_id: str
    tenant_id: str = "common"
    mailbox: str | None = None  # shared mailbox, or None for personal
    config_dir: Path | None = None
```

Drops: `server_command`, `server_args`, `server_cwd`, `server_env`.

**`connect()`** — builds authenticated HTTP client via `build_graph_client()`.

**`disconnect()`** — closes HTTP client.

**`list_messages(query, count, page_token)`:**
1. `GET /me/messages` (or `/users/{mailbox}/messages`) with `$select`, `$top`, `$orderby`, `$filter`/`$search`
2. Returns header dicts with from/subject/date/snippet (Graph API returns all this in one call)
3. `$skiptoken` or `@odata.nextLink` for pagination

**`read_message(msg_id)`:**
- `GET /me/messages/{id}` with full body
- Convert via `_msg_to_full()`

**`read_thread(thread_id)`:**
- `GET /me/messages?$filter=conversationId eq '{id}'&$orderby=receivedDateTime` (same pattern as current)
- Convert via `_thread_to_dict()`

**`discover_mailboxes()`** — keep this:
- `GET /me` → primary email + aliases from `otherMails` and `proxyAddresses`

### Step 4: Update commands.py

**`_make_adapter()` for O365** — simplified:
```python
return O365Adapter(
    O365AdapterConfig(
        client_id=cfg.get("client_id", ""),
        tenant_id=cfg.get("tenant_id", "common"),
        mailbox=cfg.get("mailbox"),
        config_dir=Path(cfg["config_dir"]) if cfg.get("config_dir") else None,
    ),
    prefix=prefix,
)
```

Drops all `server_command`, `server_args`, `server_cwd`, `server_env` handling.

### Step 5: Add `ts4k auth o365` CLI subcommand

```
ts4k auth o365 --client-id <id> --tenant-id <id>    # authenticate (device code flow)
ts4k auth o365 --client-id <id> --check              # verify credentials
```

### Step 6: Update pyproject.toml

Add to `dependencies`:
```
"msal>=1.28",
"httpx>=0.27",
```

Remove `mcp>=1.0` IF WhatsApp is also migrated. If WhatsApp still uses MCP, keep it.

### Step 7: Rewrite tests

Same pattern as Gmail test rewrite:
- **Delete:** MCP mock tests, `_make_call_result` helpers
- **New:** Graph API JSON fixtures, mock `httpx.AsyncClient` responses
- Converter pure-function tests, adapter-level tests, auth tests

### Step 8: Update discover command

`ts4k src discover` currently creates an O365Adapter to call `discover_mailboxes()`. Update to use new config shape.

## Files Changed

| File | Action | Notes |
|------|--------|-------|
| `src/ts4k/auth/microsoft.py` | Create | MSAL credential management |
| `src/ts4k/adapters/o365.py` | Rewrite | Direct Graph API, structured converters |
| `src/ts4k/commands.py` | Modify | Simplify `_make_adapter()` O365 branch |
| `src/ts4k/cli.py` | Modify | Add `ts4k auth o365` subcommand, update discover |
| `pyproject.toml` | Modify | Add msal, httpx deps |
| `tests/test_o365_adapter.py` | Rewrite | Graph API fixtures, mock httpx |
| `tests/test_microsoft_auth.py` | Create | Auth module tests |

## Key Differences from Gmail Rewrite

1. **Auth is MSAL, not OAuth2 browser flow.** Device code flow is preferred (prints a URL + code, user enters on any browser). More headless-friendly than Gmail's redirect.
2. **Graph API uses OData query syntax** (`$filter`, `$select`, `$search`, `$orderby`) vs Gmail's search query string.
3. **Thread fetch is a filtered list**, not a dedicated endpoint. Graph has no `threads.get()` — you filter messages by `conversationId`. This already works in the current adapter; keep the same pattern.
4. **Shared mailbox routing** changes the URL path (`/me/messages` vs `/users/{mailbox}/messages`), not the tool name. Simpler than the current tool-name-switching approach.
5. **Attachments** need a separate `GET /messages/{id}/attachments` call (Graph doesn't inline attachment metadata in message responses like Gmail does). May want to skip attachment details in the first pass and add later.
6. **`bodyPreview`** is Graph's equivalent of Gmail's `snippet` — already available on list responses.

## Migration

- **Credentials:** `client_id` and `tenant_id` already in `sources.json`. User runs `ts4k auth o365` once to get an MSAL token.
- **Config:** Old `server_command`/`server_args` fields silently ignored. No config changes needed.
- **Upstream server:** Can be stopped/uninstalled after migration.

## Open Questions

1. **`msgraph-sdk-python` vs `httpx` + `msal`?** Recommendation is httpx+msal for simplicity, but if the team prefers the official SDK, adjust Step 1.
2. **Attachment details:** Separate API call per message for attachments? Or defer to Phase 5? Current adapter gets them from the MCP server's response for free.
3. **`mcp` dependency:** Can it be removed from pyproject.toml? Only if WhatsApp is also migrated off MCP. Check WhatsApp adapter status first.

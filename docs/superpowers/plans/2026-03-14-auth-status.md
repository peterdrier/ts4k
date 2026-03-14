# Unified Auth Command & Token Health Validation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the subcommand-based `ts4k auth gmail/o365` with a unified `ts4k auth [target]` that resolves source prefix or provider name, add token health validation to `ts4k status`, and show token expiry after auth.

**Architecture:** A shared `TokenHealth` dataclass lives in `auth/health.py`. New `validate_token()` functions in `auth/google.py` and `auth/microsoft.py` perform lightweight checks without triggering interactive flows. A new `check_token_health()` function in `commands.py` dispatches to the right validator based on provider→auth-path mapping. The CLI `_cmd_auth()` is rewritten to accept a single positional `target` arg with source-first resolution. Status and LLM help use the same health check.

**Tech Stack:** Python 3.12+, google-auth, msal, existing ts4k state modules

**Spec:** `docs/superpowers/specs/2026-03-14-auth-status-design.md`

---

## Chunk 1: Token Health Validation Functions

### Task 1: Create `TokenHealth` dataclass and add `validate_token()` to Google auth

**Files:**
- Create: `src/ts4k/auth/health.py`
- Modify: `src/ts4k/auth/google.py`
- Test: `tests/test_google_auth.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_google_auth.py`:

```python
from ts4k.auth.google import validate_token


class TestValidateToken:
    """Tests for validate_token() — lightweight check without browser flow."""

    def test_valid_token(self, tmp_path):
        """Valid, non-expired token returns ok status."""
        from unittest.mock import MagicMock, patch
        from datetime import datetime, timezone, timedelta

        email = "alice@test.com"
        token_file = tmp_path / "google" / email / "token.json"
        token_file.parent.mkdir(parents=True)
        token_file.write_text('{"token": "x", "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_creds.scopes = ["https://www.googleapis.com/auth/gmail.readonly"]

        with patch("ts4k.auth.google.Credentials.from_authorized_user_file", return_value=mock_creds):
            result = validate_token(email, config_dir=tmp_path)

        assert result.status == "ok"
        assert result.expiry is not None
        assert len(result.scopes) > 0

    def test_expired_token_refresh_succeeds(self, tmp_path):
        """Expired token with successful refresh returns ok."""
        from unittest.mock import MagicMock, patch
        from datetime import datetime, timezone, timedelta

        email = "alice@test.com"
        token_file = tmp_path / "google" / email / "token.json"
        token_file.parent.mkdir(parents=True)
        token_file.write_text('{"token": "x", "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh-tok"
        # After refresh, expiry is updated
        mock_creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_creds.scopes = ["https://www.googleapis.com/auth/gmail.readonly"]

        with patch("ts4k.auth.google.Credentials.from_authorized_user_file", return_value=mock_creds):
            result = validate_token(email, config_dir=tmp_path)

        assert result.status == "ok"
        mock_creds.refresh.assert_called_once()

    def test_expired_token_refresh_fails(self, tmp_path):
        """Expired token with failed refresh returns auth status."""
        from unittest.mock import MagicMock, patch

        email = "alice@test.com"
        token_file = tmp_path / "google" / email / "token.json"
        token_file.parent.mkdir(parents=True)
        token_file.write_text('{"token": "x", "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]}')

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh-tok"
        mock_creds.refresh.side_effect = Exception("Token revoked")

        with patch("ts4k.auth.google.Credentials.from_authorized_user_file", return_value=mock_creds):
            result = validate_token(email, config_dir=tmp_path)

        assert result.status == "auth"
        assert "revoked" in result.detail.lower() or "expired" in result.detail.lower()

    def test_no_token_file(self, tmp_path):
        """Missing token file returns auth status."""
        result = validate_token("alice@test.com", config_dir=tmp_path)
        assert result.status == "auth"

    def test_corrupt_token_file(self, tmp_path):
        """Corrupt token file returns error status."""
        email = "alice@test.com"
        token_file = tmp_path / "google" / email / "token.json"
        token_file.parent.mkdir(parents=True)
        token_file.write_text("not json at all{{{")

        result = validate_token(email, config_dir=tmp_path)
        assert result.status == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_google_auth.py::TestValidateToken -v`
Expected: FAIL with `ImportError: cannot import name 'validate_token'`

- [ ] **Step 3: Create `TokenHealth` in `src/ts4k/auth/health.py`**

```python
"""Shared data types for token health validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TokenHealth:
    """Result of a lightweight token validation check."""
    status: str       # "ok", "auth", "error", "na"
    expiry: datetime | None
    scopes: list[str]
    detail: str       # human-readable status line
```

- [ ] **Step 4: Implement `validate_token()` in Google auth**

Add to `src/ts4k/auth/google.py` after the imports (after line 22):

```python
from ts4k.auth.health import TokenHealth
```

Add `validate_token()` after `_token_path()` (after line 55):

```python
def validate_token(
    email: str,
    config_dir: Path | None = None,
) -> TokenHealth:
    """Check token health without triggering interactive auth flows.

    Returns a TokenHealth with status:
      - "ok": token is valid (possibly after a silent refresh)
      - "auth": token missing, expired, or refresh failed — needs re-auth
      - "error": unexpected error during validation
    """
    config_dir = config_dir or _default_config_dir()
    token_file = _token_path(email, config_dir)

    if not token_file.is_file():
        return TokenHealth(
            status="auth",
            expiry=None,
            scopes=[],
            detail="no token file",
        )

    # Load token without triggering browser flow
    import json
    try:
        token_data = json.loads(token_file.read_text())
        granted = token_data.get("scopes", [])
        creds = Credentials.from_authorized_user_file(str(token_file), granted)
    except Exception as exc:
        return TokenHealth(
            status="error",
            expiry=None,
            scopes=[],
            detail=f"token load failed: {exc}",
        )

    if creds.valid:
        return TokenHealth(
            status="ok",
            expiry=creds.expiry,
            scopes=list(creds.scopes or []),
            detail="valid",
        )

    # Try silent refresh
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Persist refreshed token
            token_file.write_text(creds.to_json())
            return TokenHealth(
                status="ok",
                expiry=creds.expiry,
                scopes=list(creds.scopes or []),
                detail="refreshed",
            )
        except Exception as exc:
            return TokenHealth(
                status="auth",
                expiry=None,
                scopes=list(creds.scopes or []),
                detail=f"refresh failed: {exc}",
            )

    return TokenHealth(
        status="auth",
        expiry=None,
        scopes=list(creds.scopes or []),
        detail="token expired, no refresh token",
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_google_auth.py::TestValidateToken -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/ts4k/auth/health.py src/ts4k/auth/google.py tests/test_google_auth.py
git commit -m "feat: add TokenHealth dataclass and validate_token() to Google auth"
```

---

### Task 2: Add `validate_token()` to Microsoft auth module

**Files:**
- Modify: `src/ts4k/auth/microsoft.py`
- Test: `tests/test_microsoft_auth.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_microsoft_auth.py`:

```python
from ts4k.auth.microsoft import validate_token


class TestValidateToken:
    """Tests for validate_token() — silent check without device code flow."""

    @patch("ts4k.auth.microsoft.msal.PublicClientApplication")
    def test_valid_token(self, mock_app_cls, tmp_path):
        """Silent acquisition succeeds — returns ok with expiry."""
        from ts4k.auth.microsoft import validate_token
        from unittest.mock import MagicMock

        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@contoso.com"}]
        mock_app.acquire_token_silent.return_value = {
            "access_token": "tok-123",
            "expires_in": 3600,
        }

        mock_cache = MagicMock()
        mock_cache.has_state_changed = False

        # Create a fake cache file so it gets loaded
        cache_file = tmp_path / "microsoft" / "test-client" / "token_cache.json"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_text("{}")

        with patch("ts4k.auth.microsoft.msal.SerializableTokenCache", return_value=mock_cache):
            result = validate_token(
                "test-client",
                tenant_id="test-tenant",
                config_dir=tmp_path,
            )

        assert result.status == "ok"
        assert result.expiry is not None

    @patch("ts4k.auth.microsoft.msal.PublicClientApplication")
    def test_silent_fails(self, mock_app_cls, tmp_path):
        """Silent acquisition returns None — needs re-auth."""
        from ts4k.auth.microsoft import validate_token
        from unittest.mock import MagicMock

        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@contoso.com"}]
        mock_app.acquire_token_silent.return_value = None

        mock_cache = MagicMock()
        mock_cache.has_state_changed = False

        cache_file = tmp_path / "microsoft" / "test-client" / "token_cache.json"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_text("{}")

        with patch("ts4k.auth.microsoft.msal.SerializableTokenCache", return_value=mock_cache):
            result = validate_token(
                "test-client",
                tenant_id="test-tenant",
                config_dir=tmp_path,
            )

        assert result.status == "auth"

    def test_no_cache_file(self, tmp_path):
        """No token cache file — returns auth."""
        from ts4k.auth.microsoft import validate_token

        result = validate_token("test-client", config_dir=tmp_path)
        assert result.status == "auth"

    def test_no_client_id(self, tmp_path):
        """Empty client_id — returns na."""
        from ts4k.auth.microsoft import validate_token

        result = validate_token("", config_dir=tmp_path)
        assert result.status == "na"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_microsoft_auth.py::TestValidateToken -v`
Expected: FAIL with `ImportError: cannot import name 'validate_token'`

- [ ] **Step 3: Implement `validate_token()`**

Add at the top of `src/ts4k/auth/microsoft.py` after existing imports (after line 19):

```python
from datetime import datetime, timedelta, timezone
from ts4k.auth.health import TokenHealth
```

Add `validate_token()` after `_find_account()` (after line 130):

```python
def validate_token(
    client_id: str,
    tenant_id: str = "common",
    scopes: list[str] | None = None,
    config_dir: Path | None = None,
    username: str | None = None,
) -> TokenHealth:
    """Check token health via silent acquisition — no device code flow.

    Returns a TokenHealth with status:
      - "ok": silent token acquisition succeeded
      - "auth": no cached token or refresh failed — needs re-auth
      - "na": client_id is empty (not configured)
      - "error": unexpected error during validation
    """
    if not client_id:
        return TokenHealth(status="na", expiry=None, scopes=[], detail="no client_id")

    scopes = scopes or GRAPH_MAIL_READ_SCOPES
    config_dir = config_dir or _default_config_dir()

    cache_file = _cache_path(client_id, config_dir)
    if not cache_file.is_file():
        return TokenHealth(
            status="auth", expiry=None, scopes=[], detail="no token cache"
        )

    try:
        cache = msal.SerializableTokenCache()
        cache.deserialize(cache_file.read_text(encoding="utf-8"))

        authority = f"https://login.microsoftonline.com/{tenant_id}"
        app = msal.PublicClientApplication(
            client_id, authority=authority, token_cache=cache
        )

        accounts = app.get_accounts()
        if not accounts:
            return TokenHealth(
                status="auth", expiry=None, scopes=[], detail="no cached accounts"
            )

        account = _find_account(accounts, username)
        result = app.acquire_token_silent(scopes, account=account)

        if result and "access_token" in result:
            _persist_cache(cache, cache_file)
            expires_in = result.get("expires_in", 3600)
            expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            return TokenHealth(
                status="ok",
                expiry=expiry,
                scopes=scopes,
                detail="valid",
            )

        error_desc = ""
        if isinstance(result, dict):
            error_desc = result.get("error_description", "")
        return TokenHealth(
            status="auth",
            expiry=None,
            scopes=scopes,
            detail=f"silent acquisition failed: {error_desc}".strip(),
        )
    except Exception as exc:
        return TokenHealth(
            status="error", expiry=None, scopes=[], detail=f"validation error: {exc}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_microsoft_auth.py::TestValidateToken -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/auth/google.py src/ts4k/auth/microsoft.py tests/test_microsoft_auth.py
git commit -m "feat: add validate_token() to Microsoft auth for lightweight health checks"
```

---

### Task 3: Add `check_token_health()` dispatcher to commands.py

**Files:**
- Modify: `src/ts4k/commands.py`
- Test: `tests/test_commands.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_commands.py` (or create `tests/test_auth_health.py` if `test_commands.py` is large):

```python
from unittest.mock import patch, MagicMock
from ts4k.commands import check_token_health
from ts4k.auth.health import TokenHealth


class TestCheckTokenHealth:
    """Tests for check_token_health() provider dispatch."""

    @patch("ts4k.auth.google.validate_token")
    def test_gmail_dispatches_to_google(self, mock_validate):
        mock_validate.return_value = TokenHealth("ok", None, [], "valid")
        result = check_token_health("g", {"provider": "gmail", "email": "a@b.com"})
        assert result.status == "ok"
        mock_validate.assert_called_once()

    @patch("ts4k.auth.google.validate_token")
    def test_gcal_dispatches_to_google(self, mock_validate):
        mock_validate.return_value = TokenHealth("ok", None, [], "valid")
        result = check_token_health("gc", {"provider": "gcal", "email": "a@b.com"})
        assert result.status == "ok"
        mock_validate.assert_called_once()

    @patch("ts4k.auth.microsoft.validate_token")
    def test_o365_dispatches_to_microsoft(self, mock_validate):
        mock_validate.return_value = TokenHealth("ok", None, [], "valid")
        result = check_token_health("o", {"provider": "o365", "client_id": "cid"})
        assert result.status == "ok"
        mock_validate.assert_called_once()

    @patch("ts4k.auth.microsoft.validate_token")
    def test_o365cal_dispatches_to_microsoft(self, mock_validate):
        mock_validate.return_value = TokenHealth("ok", None, [], "valid")
        result = check_token_health("oc", {"provider": "o365cal", "client_id": "cid"})
        assert result.status == "ok"
        mock_validate.assert_called_once()

    def test_whatsapp_returns_ok(self):
        result = check_token_health("w", {"provider": "whatsapp"})
        assert result.status == "ok"

    def test_unknown_provider_returns_na(self):
        result = check_token_health("x", {"provider": "unknown"})
        assert result.status == "na"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_commands.py::TestCheckTokenHealth -v` (or `tests/test_auth_health.py::TestCheckTokenHealth`)
Expected: FAIL with `ImportError: cannot import name 'check_token_health'`

- [ ] **Step 3: Implement `check_token_health()` and helpers**

Add to `src/ts4k/commands.py` — find a good location near the existing `_sources_needing_auth()` function (around line 1893). Add before it:

```python
def check_token_health(prefix: str, cfg: dict[str, Any]) -> "TokenHealth":
    """Check token health for a source, dispatching by provider.

    Returns a TokenHealth with status ok/auth/error/na.
    WhatsApp returns ok (session-based, no token to validate).
    Unknown providers return na.

    Uses lazy imports so patches on validate_token() work correctly in tests.
    """
    from ts4k.auth.health import TokenHealth

    provider = cfg.get("provider", "").lower()

    if provider == "whatsapp":
        return TokenHealth(status="ok", expiry=None, scopes=[], detail="session-based")

    if provider in ("gmail", "gcal"):
        from ts4k.auth.google import validate_token
        email = cfg.get("email", "")
        if not email:
            return TokenHealth(status="na", expiry=None, scopes=[], detail="no email configured")
        return validate_token(email)

    if provider in ("o365", "o365cal"):
        from ts4k.auth.microsoft import validate_token
        client_id = cfg.get("client_id", "")
        tenant_id = cfg.get("tenant_id", "common") or "common"
        username = cfg.get("mailbox") or cfg.get("email")
        return validate_token(client_id, tenant_id=tenant_id, username=username)

    return TokenHealth(status="na", expiry=None, scopes=[], detail=f"unknown provider: {provider}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_commands.py::TestCheckTokenHealth -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/commands.py tests/test_commands.py
git commit -m "feat: add check_token_health() dispatcher with provider mapping"
```

---

## Chunk 2: Status Integration & Auth Command Rewrite

### Task 4: Integrate token health into `get_status()`

**Files:**
- Modify: `src/ts4k/commands.py` (lines 806-826 in `get_status()`)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_commands.py`:

```python
class TestStatusTokenHealth:
    """Tests for token health display in get_status()."""

    @patch("ts4k.commands.check_token_health")
    @patch("ts4k.commands._ensure_sources")
    @patch("ts4k.commands.contacts")
    @patch("ts4k.commands.filters")
    @patch("ts4k.commands.stats")
    def test_status_shows_health_tags(self, mock_stats, mock_filters,
                                       mock_contacts, mock_sources, mock_health):
        from datetime import datetime, timezone
        from ts4k.auth.health import TokenHealth

        mock_sources.return_value = {
            "g": {"provider": "gmail", "email": "alice@test.com"},
        }
        mock_health.return_value = TokenHealth(
            status="ok",
            expiry=datetime(2026, 3, 15, 9, 30, tzinfo=timezone.utc),
            scopes=[],
            detail="valid",
        )
        mock_contacts.list_all.return_value = {}
        mock_filters.get_config.return_value = {}
        mock_stats.get_all.return_value = {}
        mock_stats.savings_pct.return_value = 0

        result = commands.get_status()
        assert "[ok]" in result
        assert "expires" in result

    @patch("ts4k.commands.check_token_health")
    @patch("ts4k.commands._ensure_sources")
    @patch("ts4k.commands.contacts")
    @patch("ts4k.commands.filters")
    @patch("ts4k.commands.stats")
    def test_status_shows_auth_needed(self, mock_stats, mock_filters,
                                       mock_contacts, mock_sources, mock_health):
        from ts4k.auth.health import TokenHealth

        mock_sources.return_value = {
            "g": {"provider": "gmail", "email": "alice@test.com"},
        }
        mock_health.return_value = TokenHealth(
            status="auth",
            expiry=None,
            scopes=[],
            detail="token expired",
        )
        mock_contacts.list_all.return_value = {}
        mock_filters.get_config.return_value = {}
        mock_stats.get_all.return_value = {}
        mock_stats.savings_pct.return_value = 0

        result = commands.get_status()
        assert "[auth]" in result
        assert "ts4k auth g" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_commands.py::TestStatusTokenHealth -v`
Expected: FAIL — current status output uses `[ok]`/`[not found]` based on file existence, not health checks

- [ ] **Step 3: Update the Sources section of `get_status()`**

Replace the sources block in `src/ts4k/commands.py` `get_status()` (lines 806-826). The new code replaces the simple `ok`/`not found` check with `check_token_health()`:

```python
    # Sources
    lines.append("Sources:")
    if all_cfg:
        for prefix, cfg in sorted(all_cfg.items()):
            provider = cfg.get("provider", "?")
            detail = cfg.get("email") or cfg.get("mailbox") or cfg.get("mcp_cwd") or ""
            # For O365 /me sources missing email, resolve once and persist
            if provider == "o365" and not detail:
                username = _resolve_o365_username(cfg)
                if username:
                    detail = username
                    extra = {k: v for k, v in cfg.items() if k != "provider"}
                    sources.add(prefix, provider=provider, **extra, email=username)

            health = check_token_health(prefix, cfg)
            tag = health.status  # ok, auth, error, na
            suffix = ""
            if health.status == "ok" and health.expiry:
                suffix = f" expires {health.expiry.strftime('%Y-%m-%d %H:%M')}"
            elif health.status == "auth":
                suffix = f" — ts4k auth {prefix}"
            elif health.status == "error":
                suffix = f" — {health.detail}"

            lines.append(f"  {prefix:<4}{provider:<10}{detail:<30}[{tag}]{suffix}")
    else:
        lines.append("  (none — run: ts4k src add <prefix> <provider> ...)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_commands.py::TestStatusTokenHealth -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/commands.py tests/test_commands.py
git commit -m "feat: integrate token health checks into ts4k status output"
```

---

### Task 5: Refactor `_sources_needing_auth()` to use `check_token_health()`

**Files:**
- Modify: `src/ts4k/commands.py` (lines 1893-1912)

- [ ] **Step 1: Rewrite `_sources_needing_auth()`**

Replace the existing function at lines 1893-1912:

```python
def _sources_needing_auth(all_cfg: dict[str, dict[str, Any]]) -> list[str]:
    """Return prefixes of sources that likely need (re-)authentication."""
    needs: list[str] = []
    for prefix, cfg in all_cfg.items():
        health = check_token_health(prefix, cfg)
        if health.status in ("auth", "error"):
            needs.append(prefix)
    return needs
```

- [ ] **Step 2: Update LLM help auth suggestions**

In `llm_help()` (around lines 1866-1874), simplify the auth suggestion to use the new unified syntax:

Replace lines 1866-1874:

```python
        if needs_auth:
            lines.append("ACTION REQUIRED: Re-authenticate stale sources:")
            for prefix in needs_auth:
                lines.append(f"  ts4k auth {prefix}")
            lines.append("")
```

- [ ] **Step 3: Run existing tests**

Run: `uv run pytest tests/test_commands.py -v`
Expected: PASS (existing tests should still work; the function signature is unchanged)

- [ ] **Step 4: Commit**

```bash
git add src/ts4k/commands.py
git commit -m "refactor: _sources_needing_auth uses check_token_health instead of file checks"
```

---

### Task 6: Rewrite `_cmd_auth()` with unified target resolution

**Files:**
- Modify: `src/ts4k/cli.py` (lines 864-1011 `_cmd_auth`, lines 1523-1549 argparser)
- Test: `tests/test_auth_cli.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/test_auth_cli.py`:

```python
"""Tests for the unified ts4k auth CLI command."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


class TestAuthTargetResolution:
    """Tests for _cmd_auth() target resolution logic."""

    @patch("ts4k.cli.src_mod", create=True)
    def test_no_target_no_check_shows_help(self, capsys):
        """ts4k auth (no args) prints help and exits 0."""
        from ts4k.cli import _cmd_auth
        import argparse

        args = argparse.Namespace(target=None, check=False, no_calendar=False)
        with patch("ts4k.cli.sys.exit") as mock_exit:
            with patch("ts4k.state.sources.list_all", return_value={"g": {"provider": "gmail"}}):
                with patch("ts4k.state.sources.get", return_value=None):
                    _cmd_auth(args)
            mock_exit.assert_called_with(0)

    def test_bad_target_exits_1(self, capsys):
        """ts4k auth nonexistent prints error and exits 1."""
        from ts4k.cli import _cmd_auth
        import argparse

        args = argparse.Namespace(target="nonexistent", check=False, no_calendar=False)
        with patch("ts4k.cli.sys.exit") as mock_exit:
            with patch("ts4k.state.sources.list_all", return_value={"g": {"provider": "gmail"}}):
                with patch("ts4k.state.sources.get", return_value=None):
                    with patch("ts4k.state.sources.by_provider", return_value={}):
                        _cmd_auth(args)
            mock_exit.assert_called_with(1)

    def test_source_prefix_resolves(self):
        """ts4k auth g resolves to source prefix 'g'."""
        from ts4k.cli import _cmd_auth
        import argparse

        args = argparse.Namespace(target="g", check=True, no_calendar=False)
        with patch("ts4k.state.sources.get", return_value={"provider": "gmail", "email": "a@b.com"}):
            with patch("ts4k.state.sources.list_all", return_value={"g": {"provider": "gmail"}}):
                with patch("ts4k.cli._auth_check") as mock_check:
                    _cmd_auth(args)
                    mock_check.assert_called_once()
                    targets = mock_check.call_args[0][0]
                    assert len(targets) == 1
                    assert targets[0][0] == "g"

    def test_provider_name_resolves_all(self):
        """ts4k auth gmail resolves to all gmail sources."""
        from ts4k.cli import _cmd_auth
        import argparse

        args = argparse.Namespace(target="gmail", check=True, no_calendar=False)
        gmail_sources = {
            "g": {"provider": "gmail", "email": "a@b.com"},
            "gn": {"provider": "gmail", "email": "c@d.com"},
        }
        with patch("ts4k.state.sources.get", return_value=None):
            with patch("ts4k.state.sources.list_all", return_value=gmail_sources):
                with patch("ts4k.state.sources.by_provider", return_value=gmail_sources):
                    with patch("ts4k.cli._auth_check") as mock_check:
                        _cmd_auth(args)
                        targets = mock_check.call_args[0][0]
                        assert len(targets) == 2

    def test_check_all_with_no_target(self):
        """ts4k auth --check resolves to all sources."""
        from ts4k.cli import _cmd_auth
        import argparse

        all_sources = {
            "g": {"provider": "gmail", "email": "a@b.com"},
            "o": {"provider": "o365", "client_id": "cid"},
        }
        args = argparse.Namespace(target=None, check=True, no_calendar=False)
        with patch("ts4k.state.sources.list_all", return_value=all_sources):
            with patch("ts4k.cli._auth_check") as mock_check:
                _cmd_auth(args)
                targets = mock_check.call_args[0][0]
                assert len(targets) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth_cli.py -v`
Expected: FAIL (current `_cmd_auth` has different signature/structure)

- [ ] **Step 3: Replace the auth argparser**

Replace lines 1523-1549 in `src/ts4k/cli.py`:

```python
    # --- auth ---
    au = subparsers.add_parser(
        "auth",
        help="Authenticate or validate tokens",
        description=(
            "Authenticate with a messaging platform or validate existing tokens.\n\n"
            "Target resolution:\n"
            "  1. Source prefix first (g, gn, o, oc) — auths that specific source\n"
            "  2. Provider name (gmail, o365) — auths all sources of that provider\n"
            "  3. Omitted + --check — validates all sources\n"
            "  4. Omitted without --check — shows this help"
        ),
        epilog=(
            "examples:\n"
            "  ts4k auth g                  Auth source 'g' (resolves email from config)\n"
            "  ts4k auth gmail              Auth all Gmail sources\n"
            "  ts4k auth o                  Auth source 'o' (O365, device code flow)\n"
            "  ts4k auth --check            Validate all sources, no re-auth\n"
            "  ts4k auth g --check          Validate just source 'g'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    au.add_argument("target", nargs="?", default=None,
                    help="Source prefix (g, o) or provider name (gmail, o365)")
    au.add_argument("--check", action="store_true",
                    help="Validate tokens without re-auth")
    au.add_argument("--no-calendar", action="store_true",
                    help="Skip requesting calendar scopes")
    au.set_defaults(func=_cmd_auth)
```

- [ ] **Step 4: Rewrite `_cmd_auth()`**

Replace the entire `_cmd_auth` function (lines 864-1011):

```python
def _cmd_auth(args: argparse.Namespace) -> None:
    """Handle the unified auth command — authenticate or validate tokens."""
    from ts4k.state import sources as src_mod
    from ts4k.core.levels import scopes_for, parse_level, AccessLevel

    target = getattr(args, "target", None)
    check_only = getattr(args, "check", False)
    no_calendar = getattr(args, "no_calendar", False)

    all_sources = src_mod.list_all()

    # Resolve target → list of (prefix, cfg) pairs to process
    targets: list[tuple[str, dict]] = []
    if target:
        # 1. Try as source prefix
        cfg = src_mod.get(target)
        if cfg:
            targets = [(target, cfg)]
        else:
            # 2. Try as provider name
            by_prov = src_mod.by_provider(target)
            if by_prov:
                targets = list(by_prov.items())
            else:
                print(f"Error: '{target}' is not a known source prefix or provider.")
                print(f"Sources: {', '.join(all_sources.keys()) or '(none)'}")
                print(f"Providers: gmail, o365")
                sys.exit(1)
    elif check_only:
        # --check with no target → check all
        targets = list(all_sources.items())
    else:
        # No target, no --check → show help
        print("Usage: ts4k auth [target] [--check] [--no-calendar]")
        print()
        print("Target resolution:")
        print("  1. Source prefix first (g, gn, o, oc) — auths that specific source")
        print("  2. Provider name (gmail, o365) — auths all sources of that provider")
        print("  3. Omitted + --check — validates all sources")
        print("  4. Omitted without --check — shows this help")
        print()
        print("Examples:")
        print("  ts4k auth g                  Auth source 'g' (resolves email from config)")
        print("  ts4k auth gmail              Auth all Gmail sources")
        print("  ts4k auth o                  Auth source 'o' (O365, device code flow)")
        print("  ts4k auth --check            Validate all sources, no re-auth")
        print("  ts4k auth g --check          Validate just source 'g'")
        sys.exit(0)

    if not targets:
        print("No sources configured. Add one first: ts4k src add <prefix> <provider> ...")
        sys.exit(1)

    if check_only:
        _auth_check(targets)
    else:
        _auth_interactive(targets, no_calendar)


def _auth_check(targets: list[tuple[str, dict]]) -> None:
    """Validate tokens for one or more sources — no interactive flows."""
    from ts4k.commands import check_token_health

    any_bad = False
    for prefix, cfg in targets:
        health = check_token_health(prefix, cfg)
        provider = cfg.get("provider", "?")
        detail = cfg.get("email") or cfg.get("mailbox") or ""
        suffix = ""
        if health.status == "ok" and health.expiry:
            suffix = f" expires {health.expiry.strftime('%Y-%m-%d %H:%M')}"
        elif health.status == "auth":
            suffix = f" — ts4k auth {prefix}"
            any_bad = True
        elif health.status == "error":
            suffix = f" — {health.detail}"
            any_bad = True
        print(f"  {prefix:<4}{provider:<10}{detail:<30}[{health.status}]{suffix}")

    if any_bad:
        sys.exit(1)


def _auth_interactive(targets: list[tuple[str, dict]], no_calendar: bool) -> None:
    """Run interactive auth for one or more sources."""
    from ts4k.core.levels import scopes_for, parse_level, AccessLevel
    from ts4k.state import sources as src_mod

    for prefix, cfg in targets:
        provider = cfg.get("provider", "").lower()

        if provider in ("gmail", "gcal"):
            _auth_google(prefix, cfg, no_calendar)
        elif provider in ("o365", "o365cal"):
            _auth_o365(prefix, cfg, no_calendar)
        elif provider == "whatsapp":
            print(f"  {prefix}: whatsapp — session-based, no auth needed")
        else:
            print(f"  {prefix}: unknown provider '{provider}' — skipping")


def _auth_google(prefix: str, cfg: dict, no_calendar: bool) -> None:
    """Authenticate a Google source (gmail or gcal)."""
    from ts4k.auth.google import get_credentials
    from ts4k.core.levels import scopes_for, parse_level, AccessLevel
    from ts4k.state import sources as src_mod

    email = cfg.get("email", "")
    if not email:
        print(f"Error: source '{prefix}' has no email configured.")
        sys.exit(1)

    # Build scopes from source level
    source_level = cfg.get("level")
    provider = cfg.get("provider", "gmail")
    scopes = scopes_for(provider, parse_level(source_level)) or []

    # Include calendar scopes by default
    if not no_calendar:
        cal_provider = "gcal" if provider in ("gmail", "gcal") else provider
        cal_readonly_scopes = scopes_for(cal_provider, AccessLevel.READONLY)
        scopes.extend(s for s in cal_readonly_scopes if s not in scopes)

        # Collect higher gcal scopes if gcal sources exist for this email
        all_sources = src_mod.list_all()
        for pfx, src_cfg in all_sources.items():
            if src_cfg.get("provider") == "gcal" and src_cfg.get("email") == email:
                gcal_level = parse_level(src_cfg.get("level"))
                gcal_scopes = scopes_for("gcal", gcal_level)
                scopes.extend(s for s in gcal_scopes if s not in scopes)

    try:
        creds = get_credentials(email, scopes=scopes or None)
        print(f"Authenticated {prefix} ({email}) successfully.")

        # Show granted scopes
        granted = set(creds.scopes or [])
        scope_labels = sorted(s.rsplit("/", 1)[-1] for s in granted)
        print(f"Scopes: {', '.join(scope_labels)}")

        # Show expiry
        if creds.expiry:
            print(f"Expires: {creds.expiry.strftime('%Y-%m-%d %H:%M')}")
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        print(f"Run: ts4k auth {prefix}")
        sys.exit(1)
    except Exception as exc:
        print(f"Authentication failed for {prefix}: {exc}")
        sys.exit(1)


def _auth_o365(prefix: str, cfg: dict, no_calendar: bool) -> None:
    """Authenticate an O365 source (o365 or o365cal)."""
    from ts4k.auth.microsoft import get_credentials as get_ms_credentials
    from ts4k.core.levels import scopes_for, parse_level, AccessLevel
    from ts4k.state import sources as src_mod
    from datetime import datetime, timedelta, timezone

    client_id = cfg.get("client_id", "")
    tenant_id = cfg.get("tenant_id", "common") or "common"

    if not client_id:
        print(f"Error: source '{prefix}' is missing client_id.")
        print(f"Fix it: ts4k src add {prefix} o365 client_id=<id> tenant_id=<tid>")
        sys.exit(1)

    # Build scopes from source level
    source_level = cfg.get("level")
    provider = cfg.get("provider", "o365")
    scopes = scopes_for(provider, parse_level(source_level)) or []

    # Include calendar scopes by default
    if not no_calendar:
        cal_readonly_scopes = scopes_for("o365cal", AccessLevel.READONLY)
        scopes.extend(s for s in cal_readonly_scopes if s not in scopes)

        # Collect higher o365cal scopes if they exist for this client_id
        all_sources = src_mod.list_all()
        for pfx, src_cfg in all_sources.items():
            if src_cfg.get("provider") == "o365cal" and src_cfg.get("client_id") == client_id:
                cal_level = parse_level(src_cfg.get("level"))
                cal_scopes = scopes_for("o365cal", cal_level)
                scopes.extend(s for s in cal_scopes if s not in scopes)

    try:
        creds = get_ms_credentials(client_id, tenant_id=tenant_id, scopes=scopes or None)
        print(f"Authenticated {prefix} (client {client_id[:8]}...) successfully.")

        # Show expiry
        expires_in = creds.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        print(f"Expires: {expiry.strftime('%Y-%m-%d %H:%M')}")
    except Exception as exc:
        print(f"Authentication failed for {prefix}: {exc}")
        sys.exit(1)
```

- [ ] **Step 5: Run full test suite (including CLI tests)**

Run: `uv run pytest -x -v`
Expected: All tests PASS (including `tests/test_auth_cli.py`)

- [ ] **Step 6: Commit**

```bash
git add src/ts4k/cli.py tests/test_auth_cli.py
git commit -m "feat: rewrite auth command with unified target resolution

ts4k auth [target] resolves source prefix first, then provider name.
--check validates without re-auth. Shows expiry after auth.
Replaces ts4k auth gmail/o365 subcommands."
```

---

## Chunk 3: Error Messages & Documentation Updates

### Task 7: Update error messages and help text

**Files:**
- Modify: `src/ts4k/commands.py` (lines 1955, 1961, 1980)
- Modify: `src/ts4k/cli.py` (lines 203, 222-224)

- [ ] **Step 1: Update `_append_setup()` in commands.py**

In `src/ts4k/commands.py`, replace line 1955:
```python
    lines.append("    4. ts4k auth gmail <email>                  (opens browser for OAuth)")
```
with:
```python
    lines.append("    4. ts4k auth g                              (opens browser for OAuth)")
```

Replace line 1961:
```python
    lines.append("    3. ts4k auth o365                           (device code flow)")
```
with:
```python
    lines.append("    3. ts4k auth o                              (device code flow)")
```

- [ ] **Step 2: Update `_append_errors()` in commands.py**

Replace line 1980:
```python
    lines.append('  "auth expired" -> ts4k auth gmail <email> (in a terminal with browser)')
```
with:
```python
    lines.append('  "auth expired" -> ts4k auth <prefix> (in a terminal with browser)')
```

- [ ] **Step 3: Update help text in cli.py**

Replace line 203:
```python
    print("  auth gmail|o365                              Authenticate with a platform")
```
with:
```python
    print("  auth [source|provider]                       Authenticate or validate tokens")
```

Replace lines 222-224:
```python
        print("Quick setup:")
        print("  1. ts4k src add g gmail email=you@gmail.com")
        print("  2. ts4k auth gmail you@gmail.com")
```
with:
```python
        print("Quick setup:")
        print("  1. ts4k src add g gmail email=you@gmail.com")
        print("  2. ts4k auth g")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ts4k/commands.py src/ts4k/cli.py
git commit -m "fix: update all auth error messages to use unified ts4k auth <prefix> syntax"
```

---

### Task 8: Update documentation

**Files:**
- Modify: `docs/setup-gmail.md` (lines 74, 90, 119, 135-136, 159)
- Modify: `docs/setup-o365.md` (lines 100, 126, 133, 168)
- Modify: `docs/usage.md` (lines 171-179)

- [ ] **Step 1: Update setup-gmail.md**

Line 74 — replace `ts4k auth gmail alice@gmail.com` with `ts4k auth g`

Line 90 — replace `$ ts4k auth gmail alice@gmail.com` with `$ ts4k auth g`

Line 119 — replace `run \`ts4k auth gmail alice@gmail.com\` again` with `run \`ts4k auth g\` again`

Lines 135-136 — replace:
```
ts4k auth gmail alice@gmail.com
ts4k auth gmail alice@company.com
```
with:
```
ts4k auth g
ts4k auth gw
```

Line 159 — replace `Run \`ts4k auth gmail alice@gmail.com\`` with `Run \`ts4k auth g\``

- [ ] **Step 2: Update setup-o365.md**

Line 100 — replace `ts4k auth o365` with `ts4k auth o`

Line 126 — replace `ts4k auth o365 ow` with `ts4k auth ow`

Line 133 — replace `ts4k auth o365 os` with `ts4k auth os`

Line 168 — replace `ts4k auth o365 oh` with `ts4k auth oh`

- [ ] **Step 3: Update usage.md**

Replace lines 171-179:
```markdown
### Gmail

```bash
ts4k auth g                    # OAuth flow (opens browser)
ts4k auth g --check            # Verify credentials
```

### O365

```bash
ts4k auth o                    # Device code flow
ts4k auth o --check            # Verify credentials
```
```

- [ ] **Step 4: Commit**

```bash
git add docs/setup-gmail.md docs/setup-o365.md docs/usage.md
git commit -m "docs: update auth command syntax to unified ts4k auth <prefix>"
```

---

### Task 9: Final verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS

- [ ] **Step 2: Manual smoke test**

Run these commands and verify output:
```bash
ts4k auth --help          # Should show unified help with examples
ts4k auth --check         # Should show all sources with [ok]/[auth] tags and expiry
ts4k status               # Sources section should show [ok]/[auth] with expiry dates
```

- [ ] **Step 3: Commit any final fixes**

If smoke tests revealed issues, fix and commit.

- [ ] **Step 4: Final commit message**

If everything passed with no fixes needed, this step is a no-op. Otherwise commit any remaining fixes.

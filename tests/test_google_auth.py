"""Tests for the Google OAuth credential management module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ts4k.auth.google import (
    _default_config_dir,
    _resolve_client_secret,
    _token_path,
    get_credentials,
    validate_token,
)


# ---------------------------------------------------------------------------
# Path resolution tests
# ---------------------------------------------------------------------------


class TestResolveClientSecret:
    """Tests for _resolve_client_secret() resolution chain."""

    def test_per_account_path_first(self, tmp_path):
        """Per-account client_secret.json takes priority."""
        per_account = tmp_path / "google" / "alice@test.com" / "client_secret.json"
        per_account.parent.mkdir(parents=True)
        per_account.write_text("{}")

        shared = tmp_path / "google" / "client_secret.json"
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text("{}")

        result = _resolve_client_secret("alice@test.com", tmp_path)
        assert result == per_account

    def test_shared_path_second(self, tmp_path):
        """Shared client_secret.json used when per-account doesn't exist."""
        shared = tmp_path / "google" / "client_secret.json"
        shared.parent.mkdir(parents=True)
        shared.write_text("{}")

        result = _resolve_client_secret("alice@test.com", tmp_path)
        assert result == shared

    def test_none_when_no_secret_found(self, tmp_path):
        """Returns None when no client_secret.json exists anywhere."""
        result = _resolve_client_secret("alice@test.com", tmp_path)
        assert result is None


class TestTokenPath:
    """Tests for _token_path()."""

    def test_per_account_token_path(self):
        config_dir = Path("/config/ts4k")
        result = _token_path("alice@test.com", config_dir)
        assert result == Path("/config/ts4k/google/alice@test.com/token.json")

    def test_default_config_dir(self):
        result = _token_path("bob@test.com", _default_config_dir())
        assert "google" in str(result)
        assert "bob@test.com" in str(result)
        assert result.name == "token.json"


# ---------------------------------------------------------------------------
# Credential loading tests
# ---------------------------------------------------------------------------


class TestGetCredentials:
    """Tests for get_credentials() with mocked filesystem and google-auth."""

    def test_loads_existing_valid_token(self, tmp_path):
        """Valid existing token is returned without re-auth."""
        import json
        token_file = tmp_path / "google" / "user@test.com" / "token.json"
        token_file.parent.mkdir(parents=True)
        token_file.write_text(json.dumps({
            "token": "mock",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }))

        mock_creds = MagicMock()
        mock_creds.valid = True

        with patch(
            "ts4k.auth.google.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ):
            result = get_credentials("user@test.com", config_dir=tmp_path)
            assert result is mock_creds

    def test_reauth_when_scopes_insufficient(self, tmp_path):
        """Token with insufficient scopes triggers re-auth; new token replaces it."""
        import json
        token_file = tmp_path / "google" / "user@test.com" / "token.json"
        token_file.parent.mkdir(parents=True)
        # Token was granted readonly scopes
        token_file.write_text(json.dumps({
            "token": "mock",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }))

        # Request modify scopes (upgrade)
        modify_scopes = ["https://www.googleapis.com/auth/gmail.modify"]

        # Set up mock for the re-auth flow
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.granted_scopes = modify_scopes
        mock_creds.to_json.return_value = json.dumps({"token": "new", "scopes": modify_scopes})

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds

        # Create client_secret.json for the OAuth flow
        secret_file = tmp_path / "google" / "user@test.com" / "client_secret.json"
        secret_file.write_text('{"installed": {"client_id": "test"}}')

        with patch(
            "ts4k.auth.google.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ):
            result = get_credentials(
                "user@test.com", scopes=modify_scopes, config_dir=tmp_path,
            )
            assert result is mock_creds
            # New token overwrites the old one after successful re-auth
            assert json.loads(token_file.read_text())["scopes"] == modify_scopes

    def test_underscoped_token_preserved_when_flow_fails(self, tmp_path):
        """If re-auth fails (e.g. headless), the old under-scoped token survives."""
        import json
        token_file = tmp_path / "google" / "user@test.com" / "token.json"
        token_file.parent.mkdir(parents=True)
        original = json.dumps({
            "token": "mock",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        })
        token_file.write_text(original)

        secret_file = tmp_path / "google" / "user@test.com" / "client_secret.json"
        secret_file.write_text('{"installed": {"client_id": "test"}}')

        mock_flow = MagicMock()
        mock_flow.run_local_server.side_effect = Exception("no browser")

        with patch(
            "ts4k.auth.google.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ), patch("ts4k.auth.google.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(RuntimeError):
                get_credentials(
                    "user@test.com",
                    scopes=["https://www.googleapis.com/auth/gmail.modify"],
                    config_dir=tmp_path,
                )

        assert token_file.is_file()
        assert token_file.read_text() == original

    def test_refreshes_expired_token(self, tmp_path):
        """Expired token with refresh_token is refreshed."""
        import json
        token_file = tmp_path / "google" / "user@test.com" / "token.json"
        token_file.parent.mkdir(parents=True)
        token_file.write_text(json.dumps({
            "token": "mock",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        }))

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_tok"
        mock_creds.to_json.return_value = '{"refreshed": true}'

        # After refresh, creds become valid.
        def do_refresh(request):
            mock_creds.valid = True

        mock_creds.refresh = do_refresh

        with patch(
            "ts4k.auth.google.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ), patch("ts4k.auth.google.Request"):
            result = get_credentials("user@test.com", config_dir=tmp_path)
            assert result is mock_creds

    def test_refresh_preserves_stored_scopes(self, tmp_path):
        """A refresh triggered by a narrow-scope caller must not drop the
        other granted scopes from the token record on disk."""
        import json
        stored = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
        ]
        token_file = tmp_path / "google" / "user@test.com" / "token.json"
        token_file.parent.mkdir(parents=True)
        token_file.write_text(json.dumps({"token": "mock", "scopes": stored}))

        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_tok"
        mock_creds.to_json.return_value = json.dumps(
            {"token": "refreshed", "scopes": stored}
        )

        def do_refresh(request):
            mock_creds.valid = True

        mock_creds.refresh = do_refresh

        with patch(
            "ts4k.auth.google.Credentials.from_authorized_user_file",
            return_value=mock_creds,
        ) as mock_load, patch("ts4k.auth.google.Request"):
            get_credentials(
                "user@test.com",
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                config_dir=tmp_path,
            )

        # Credentials must be loaded with the STORED scope set, so the
        # to_json() rewrite after refresh keeps all granted scopes.
        assert set(mock_load.call_args.args[1]) == set(stored)

    def test_missing_client_secret_raises(self, tmp_path):
        """FileNotFoundError raised when no client_secret.json exists."""
        with patch(
            "ts4k.auth.google._resolve_client_secret",
            return_value=None,
        ):
            with pytest.raises(FileNotFoundError, match="client_secret.json"):
                get_credentials("noone@test.com", config_dir=tmp_path)

    def test_resolution_chain_order(self, tmp_path):
        """Per-account secret is tried before shared."""
        # Only create shared — per-account doesn't exist.
        shared = tmp_path / "google" / "client_secret.json"
        shared.parent.mkdir(parents=True)
        shared.write_text('{"installed": {"client_id": "test"}}')

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.granted_scopes = None

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds
        mock_creds.to_json.return_value = '{"token": "new"}'

        with patch(
            "ts4k.auth.google.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ) as mock_from_file:
            result = get_credentials("user@test.com", config_dir=tmp_path)
            # Should have used the shared path with the default scopes.
            mock_from_file.assert_called_once_with(
                str(shared),
                ["https://www.googleapis.com/auth/gmail.readonly"],
            )


# ---------------------------------------------------------------------------
# Under-granting detection tests
# ---------------------------------------------------------------------------


class TestScopeVerification:
    """After a new auth, granted scopes are compared against requested ones."""

    def _run_flow(self, tmp_path, requested, granted_scopes):
        """Run get_credentials with a mocked flow that grants *granted_scopes*.

        Mirrors google_auth_oauthlib behavior: ``creds.scopes`` holds the
        REQUESTED scopes; the actual grant lives in ``creds.granted_scopes``,
        and ``to_json()`` serializes the requested set.
        """
        import json
        secret_file = tmp_path / "google" / "user@test.com" / "client_secret.json"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text('{"installed": {"client_id": "test"}}')

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.scopes = list(requested)
        mock_creds.granted_scopes = granted_scopes
        mock_creds.to_json.return_value = json.dumps(
            {"token": "new", "scopes": list(requested)}
        )

        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds

        with patch(
            "ts4k.auth.google.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ):
            return get_credentials(
                "user@test.com", scopes=requested, config_dir=tmp_path,
            )

    def test_warns_when_google_grants_fewer_scopes(self, tmp_path, caplog):
        """Under-granting is logged explicitly, naming the missing scopes."""
        import logging
        requested = [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar.readonly",
        ]
        granted = ["https://www.googleapis.com/auth/calendar.readonly"]

        with caplog.at_level(logging.WARNING, logger="ts4k.auth.google"):
            self._run_flow(tmp_path, requested, granted)

        assert "gmail.modify" in caplog.text
        assert "fewer scopes" in caplog.text

    def test_undergranted_token_still_written(self, tmp_path):
        """The token is persisted even when under-granted, recording the
        GRANTED scopes — not the requested set that to_json() serializes."""
        import json
        requested = ["https://www.googleapis.com/auth/gmail.modify"]
        granted = ["https://www.googleapis.com/auth/calendar.readonly"]

        self._run_flow(tmp_path, requested, granted)

        token_file = tmp_path / "google" / "user@test.com" / "token.json"
        assert json.loads(token_file.read_text())["scopes"] == granted

    def test_no_warning_when_all_scopes_granted(self, tmp_path, caplog):
        import logging
        requested = ["https://www.googleapis.com/auth/gmail.readonly"]

        with caplog.at_level(logging.WARNING, logger="ts4k.auth.google"):
            self._run_flow(tmp_path, requested, list(requested))

        assert "fewer scopes" not in caplog.text

    def test_falls_back_to_scopes_when_granted_scopes_missing(self, tmp_path, caplog):
        """If the server omitted scope info (granted_scopes is None), trust
        creds.scopes and don't warn."""
        import logging
        requested = ["https://www.googleapis.com/auth/gmail.readonly"]

        with caplog.at_level(logging.WARNING, logger="ts4k.auth.google"):
            self._run_flow(tmp_path, requested, None)

        assert "fewer scopes" not in caplog.text

    def test_relax_token_scope_env_set_during_flow(self, tmp_path, monkeypatch):
        """oauthlib must not abort the flow when Google grants fewer scopes —
        we detect and report the discrepancy ourselves after the flow."""
        import json
        import os

        monkeypatch.delenv("OAUTHLIB_RELAX_TOKEN_SCOPE", raising=False)

        secret_file = tmp_path / "google" / "user@test.com" / "client_secret.json"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text('{"installed": {"client_id": "test"}}')

        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        mock_creds.granted_scopes = None
        mock_creds.to_json.return_value = json.dumps({"token": "new"})

        seen = {}

        def run_local_server(**kwargs):
            seen["relax"] = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
            return mock_creds

        mock_flow = MagicMock()
        mock_flow.run_local_server.side_effect = run_local_server

        with patch(
            "ts4k.auth.google.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ):
            get_credentials("user@test.com", config_dir=tmp_path)

        assert seen["relax"] == "1"


# ---------------------------------------------------------------------------
# Per-email scope union tests
# ---------------------------------------------------------------------------


class TestUnionScopesForEmail:
    """Tests for union_scopes_for_email() — gmail/gcal share one token per email."""

    def test_unions_across_gmail_and_gcal_sources(self):
        from ts4k.auth.google import union_scopes_for_email

        srcs = {
            "g": {"provider": "gmail", "email": "a@b.com"},
            "gcp": {"provider": "gcal", "email": "a@b.com", "level": "draft"},
            "x": {"provider": "gmail", "email": "other@b.com", "level": "modify"},
        }
        with patch("ts4k.state.sources.list_all", return_value=srcs):
            scopes = union_scopes_for_email("a@b.com")

        assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
        assert "https://www.googleapis.com/auth/calendar" in scopes
        # Scopes from a different email must not leak in.
        assert "https://www.googleapis.com/auth/gmail.modify" not in scopes

    def test_includes_calendar_readonly_by_default(self):
        from ts4k.auth.google import union_scopes_for_email

        srcs = {"g": {"provider": "gmail", "email": "a@b.com"}}
        with patch("ts4k.state.sources.list_all", return_value=srcs):
            scopes = union_scopes_for_email("a@b.com")

        assert "https://www.googleapis.com/auth/calendar.readonly" in scopes

    def test_no_calendar_readonly_when_disabled(self):
        from ts4k.auth.google import union_scopes_for_email

        srcs = {"g": {"provider": "gmail", "email": "a@b.com"}}
        with patch("ts4k.state.sources.list_all", return_value=srcs):
            scopes = union_scopes_for_email("a@b.com", include_calendar_readonly=False)

        assert scopes == ["https://www.googleapis.com/auth/gmail.readonly"]

    def test_no_duplicate_scopes(self):
        from ts4k.auth.google import union_scopes_for_email

        srcs = {
            "g1": {"provider": "gmail", "email": "a@b.com", "level": "modify"},
            "g2": {"provider": "gmail", "email": "a@b.com", "level": "draft"},
        }
        with patch("ts4k.state.sources.list_all", return_value=srcs):
            scopes = union_scopes_for_email("a@b.com")

        assert scopes.count("https://www.googleapis.com/auth/gmail.modify") == 1


# ---------------------------------------------------------------------------
# Token health validation tests
# ---------------------------------------------------------------------------


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
        mock_creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_creds.scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        mock_creds.to_json.return_value = '{"token": "refreshed"}'

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


def test_build_calendar_service(monkeypatch):
    """build_calendar_service calls get_credentials and builds calendar v3."""
    from unittest.mock import MagicMock, patch
    from ts4k.auth.google import build_calendar_service

    mock_creds = MagicMock()
    mock_service = MagicMock()

    with patch("ts4k.auth.google.get_credentials", return_value=mock_creds) as mock_get, \
         patch("ts4k.auth.google.build", return_value=mock_service) as mock_build:
        result = build_calendar_service("test@gmail.com", scopes=["calendar.readonly"])

    mock_get.assert_called_once_with("test@gmail.com", ["calendar.readonly"], None)
    mock_build.assert_called_once_with("calendar", "v3", credentials=mock_creds)
    assert result is mock_service

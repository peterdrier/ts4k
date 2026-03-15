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

    def test_deletes_token_when_scopes_insufficient(self, tmp_path):
        """Token with insufficient scopes is deleted and re-auth triggered."""
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
            # Old token file should have been deleted and replaced
            assert token_file.is_file()  # new token saved

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

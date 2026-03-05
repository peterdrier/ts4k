"""Tests for Microsoft OAuth credential management.

All tests mock ``msal.PublicClientApplication`` so no real auth is needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ts4k.auth.microsoft import (
    GRAPH_MAIL_READ_SCOPES,
    _cache_path,
    build_graph_client,
    get_credentials,
)


# ---------------------------------------------------------------------------
# _cache_path
# ---------------------------------------------------------------------------


class TestCachePath:
    def test_default_path(self):
        p = _cache_path("my-client-id", Path("/fake/config"))
        assert p == Path("/fake/config/microsoft/my-client-id/token_cache.json")

    def test_different_client_id(self):
        p = _cache_path("other-id", Path("/cfg"))
        assert p == Path("/cfg/microsoft/other-id/token_cache.json")


# ---------------------------------------------------------------------------
# get_credentials
# ---------------------------------------------------------------------------


class TestGetCredentials:
    """Tests for get_credentials() with mocked MSAL."""

    @patch("ts4k.auth.microsoft.msal.PublicClientApplication")
    def test_silent_token_load(self, mock_app_cls, tmp_path):
        """When a cached token exists, acquire_token_silent succeeds."""
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@contoso.com"}]
        mock_app.acquire_token_silent.return_value = {
            "access_token": "silent-token-123",
        }
        # Provide a mock cache that doesn't need persistence
        mock_cache = MagicMock()
        mock_cache.has_state_changed = False

        with patch("ts4k.auth.microsoft.msal.SerializableTokenCache", return_value=mock_cache):
            result = get_credentials(
                "test-client-id",
                tenant_id="test-tenant",
                config_dir=tmp_path,
            )

        assert result["access_token"] == "silent-token-123"
        mock_app.acquire_token_silent.assert_called_once()
        mock_app.initiate_device_flow.assert_not_called()

    @patch("ts4k.auth.microsoft.msal.PublicClientApplication")
    def test_device_flow_trigger(self, mock_app_cls, tmp_path):
        """When no cached token, falls back to device code flow."""
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []
        mock_app.initiate_device_flow.return_value = {
            "user_code": "ABC123",
            "message": "Go to https://microsoft.com/devicelogin and enter ABC123",
        }
        mock_app.acquire_token_by_device_flow.return_value = {
            "access_token": "device-flow-token",
        }
        mock_cache = MagicMock()
        mock_cache.has_state_changed = True
        mock_cache.serialize.return_value = '{"cached": true}'

        with patch("ts4k.auth.microsoft.msal.SerializableTokenCache", return_value=mock_cache):
            result = get_credentials(
                "test-client-id",
                config_dir=tmp_path,
            )

        assert result["access_token"] == "device-flow-token"
        mock_app.initiate_device_flow.assert_called_once()
        mock_app.acquire_token_by_device_flow.assert_called_once()

    @patch("ts4k.auth.microsoft.msal.PublicClientApplication")
    def test_cache_persistence(self, mock_app_cls, tmp_path):
        """Token cache is written to disk when state has changed."""
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []
        mock_app.initiate_device_flow.return_value = {
            "user_code": "XYZ",
            "message": "Visit ...",
        }
        mock_app.acquire_token_by_device_flow.return_value = {
            "access_token": "new-token",
        }
        mock_cache = MagicMock()
        mock_cache.has_state_changed = True
        mock_cache.serialize.return_value = '{"cached": true}'

        with patch("ts4k.auth.microsoft.msal.SerializableTokenCache", return_value=mock_cache):
            get_credentials("my-app", config_dir=tmp_path)

        cache_file = tmp_path / "microsoft" / "my-app" / "token_cache.json"
        assert cache_file.is_file()
        assert cache_file.read_text(encoding="utf-8") == '{"cached": true}'

    def test_empty_client_id_raises(self):
        with pytest.raises(ValueError, match="client_id is required"):
            get_credentials("")

    @patch("ts4k.auth.microsoft.msal.PublicClientApplication")
    def test_device_flow_initiation_failure(self, mock_app_cls, tmp_path):
        """RuntimeError when device flow initiation fails."""
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []
        mock_app.initiate_device_flow.return_value = {
            "error_description": "Bad client_id",
        }
        mock_cache = MagicMock()
        mock_cache.has_state_changed = False

        with patch("ts4k.auth.microsoft.msal.SerializableTokenCache", return_value=mock_cache):
            with pytest.raises(RuntimeError, match="Device flow initiation failed"):
                get_credentials("bad-id", config_dir=tmp_path)

    @patch("ts4k.auth.microsoft.msal.PublicClientApplication")
    def test_auth_failure_raises(self, mock_app_cls, tmp_path):
        """RuntimeError when token acquisition fails."""
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []
        mock_app.initiate_device_flow.return_value = {
            "user_code": "ABC",
            "message": "Visit ...",
        }
        mock_app.acquire_token_by_device_flow.return_value = {
            "error_description": "User cancelled",
        }
        mock_cache = MagicMock()
        mock_cache.has_state_changed = False

        with patch("ts4k.auth.microsoft.msal.SerializableTokenCache", return_value=mock_cache):
            with pytest.raises(RuntimeError, match="Authentication failed"):
                get_credentials("test-id", config_dir=tmp_path)

    @patch("ts4k.auth.microsoft.msal.PublicClientApplication")
    def test_authority_uses_tenant_id(self, mock_app_cls, tmp_path):
        """Authority URL includes the provided tenant_id."""
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "u"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "t"}
        mock_cache = MagicMock()
        mock_cache.has_state_changed = False

        with patch("ts4k.auth.microsoft.msal.SerializableTokenCache", return_value=mock_cache):
            get_credentials("cid", tenant_id="my-tenant", config_dir=tmp_path)

        mock_app_cls.assert_called_once_with(
            "cid",
            authority="https://login.microsoftonline.com/my-tenant",
            token_cache=mock_cache,
        )


# ---------------------------------------------------------------------------
# build_graph_client
# ---------------------------------------------------------------------------


class TestBuildGraphClient:
    """Tests for build_graph_client()."""

    @patch("ts4k.auth.microsoft.get_credentials")
    def test_base_url_set(self, mock_get_creds):
        mock_get_creds.return_value = {"access_token": "test-token"}
        client = build_graph_client("cid", tenant_id="tid")
        assert str(client.base_url).rstrip("/") == "https://graph.microsoft.com/v1.0"

    @patch("ts4k.auth.microsoft.get_credentials")
    def test_auth_header_present(self, mock_get_creds):
        mock_get_creds.return_value = {"access_token": "my-bearer-token"}
        client = build_graph_client("cid")
        assert client.headers["authorization"] == "Bearer my-bearer-token"

    @patch("ts4k.auth.microsoft.get_credentials")
    def test_passes_config_dir(self, mock_get_creds):
        mock_get_creds.return_value = {"access_token": "t"}
        build_graph_client("cid", config_dir=Path("/custom"))
        mock_get_creds.assert_called_once_with(
            "cid", tenant_id="common", scopes=None, config_dir=Path("/custom"),
            username=None,
        )

"""Tests for check_token_health() provider dispatch."""

from unittest.mock import patch

from ts4k.auth.health import TokenHealth
from ts4k.commands import check_token_health


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

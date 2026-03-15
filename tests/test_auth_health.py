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
        from ts4k import commands

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

    @patch("ts4k.commands.check_token_health")
    @patch("ts4k.commands._ensure_sources")
    @patch("ts4k.commands.contacts")
    @patch("ts4k.commands.filters")
    @patch("ts4k.commands.stats")
    def test_status_shows_auth_needed(self, mock_stats, mock_filters,
                                       mock_contacts, mock_sources, mock_health):
        from ts4k.auth.health import TokenHealth
        from ts4k import commands

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

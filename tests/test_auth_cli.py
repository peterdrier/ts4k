"""Tests for the unified ts4k auth CLI command."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest


class TestAuthTargetResolution:
    """Tests for _cmd_auth() target resolution logic."""

    def test_no_target_no_check_shows_help(self):
        """ts4k auth (no args) prints help and exits 0."""
        from ts4k.cli import _cmd_auth
        import argparse

        args = argparse.Namespace(target=None, check=False, no_calendar=False)
        with patch("ts4k.state.sources.list_all", return_value={"g": {"provider": "gmail"}}):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_auth(args)
            assert exc_info.value.code == 0

    def test_bad_target_exits_1(self):
        """ts4k auth nonexistent prints error and exits 1."""
        from ts4k.cli import _cmd_auth
        import argparse

        args = argparse.Namespace(target="nonexistent", check=False, no_calendar=False)
        with patch("ts4k.state.sources.list_all", return_value={"g": {"provider": "gmail"}}):
            with patch("ts4k.state.sources.get", return_value=None):
                with patch("ts4k.state.sources.by_provider", return_value={}):
                    with pytest.raises(SystemExit) as exc_info:
                        _cmd_auth(args)
                    assert exc_info.value.code == 1

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

    def test_auth_google_reports_undergranted_scopes(self, capsys):
        """When Google grants fewer scopes than requested, say which are
        missing and point at the app registration / Workspace policy."""
        from ts4k.cli import _auth_google

        cfg = {"provider": "gmail", "email": "a@b.com", "level": "modify"}
        mock_creds = MagicMock()
        # Real google-auth behavior: .scopes echoes the REQUESTED set even
        # when Google under-grants; the actual grant is in granted_scopes.
        mock_creds.scopes = [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar.readonly",
        ]
        mock_creds.granted_scopes = ["https://www.googleapis.com/auth/calendar.readonly"]

        with patch("ts4k.auth.google.get_credentials", return_value=mock_creds):
            with patch("ts4k.state.sources.list_all", return_value={"gn": cfg}):
                _auth_google("gn", cfg, no_calendar=False)

        out = capsys.readouterr().out
        assert "gmail.modify" in out
        assert "fewer scopes" in out.lower()
        assert "app registration" in out.lower() or "workspace" in out.lower()

    def test_auth_google_no_warning_when_fully_granted(self, capsys):
        from ts4k.cli import _auth_google

        cfg = {"provider": "gmail", "email": "a@b.com"}
        mock_creds = MagicMock()
        mock_creds.scopes = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
        ]
        mock_creds.granted_scopes = list(mock_creds.scopes)

        with patch("ts4k.auth.google.get_credentials", return_value=mock_creds):
            with patch("ts4k.state.sources.list_all", return_value={"g": cfg}):
                _auth_google("g", cfg, no_calendar=False)

        out = capsys.readouterr().out
        assert "fewer scopes" not in out.lower()

    def test_auth_check_shows_missing_scope_detail(self, capsys):
        """auth --check surfaces the under-scope detail, not just [auth]."""
        from ts4k.cli import _auth_check
        from ts4k.auth.health import TokenHealth

        health = TokenHealth(
            status="auth",
            expiry=None,
            scopes=[],
            detail="missing scopes: gmail.modify",
        )
        targets = [("gn", {"provider": "gmail", "email": "a@b.com"})]

        with patch("ts4k.commands.check_token_health", return_value=health):
            with pytest.raises(SystemExit):
                _auth_check(targets)

        out = capsys.readouterr().out
        assert "missing scopes: gmail.modify" in out
        assert "ts4k auth gn" in out

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

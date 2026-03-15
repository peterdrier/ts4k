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

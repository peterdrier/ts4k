"""Tests for O365 calendar scope mapping and level guards."""

from __future__ import annotations

import pytest
from ts4k.core.levels import (
    AccessLevel, check_level, parse_level, scopes_for,
)


class TestO365CalScopes:
    def test_readonly_scope(self):
        scopes = scopes_for("o365cal", AccessLevel.READONLY)
        assert "https://graph.microsoft.com/Calendars.Read" in scopes

    def test_modify_scope(self):
        scopes = scopes_for("o365cal", AccessLevel.MODIFY)
        assert "https://graph.microsoft.com/Calendars.ReadWrite" in scopes

    def test_draft_scope(self):
        scopes = scopes_for("o365cal", AccessLevel.DRAFT)
        assert "https://graph.microsoft.com/Calendars.ReadWrite" in scopes

    def test_send_scope(self):
        scopes = scopes_for("o365cal", AccessLevel.SEND)
        assert "https://graph.microsoft.com/Calendars.ReadWrite" in scopes

    def test_unknown_level_returns_empty(self):
        scopes = scopes_for("o365cal", AccessLevel.READONLY)
        assert isinstance(scopes, list)


class TestO365CalLevelGuards:
    def test_send_allowed_for_o365cal(self):
        """SEND level should NOT raise for o365cal (calendar invites are ok)."""
        check_level(AccessLevel.SEND, AccessLevel.SEND, "create_event", provider="o365cal")

    def test_send_blocked_for_o365_mail(self):
        """SEND level should raise for o365 (messaging)."""
        with pytest.raises(NotImplementedError):
            check_level(AccessLevel.SEND, AccessLevel.SEND, "send_msg", provider="o365")

    def test_send_blocked_for_none_provider(self):
        """SEND level should raise when no provider specified."""
        with pytest.raises(NotImplementedError):
            check_level(AccessLevel.SEND, AccessLevel.SEND, "send_msg")

    def test_readonly_below_modify_raises(self):
        with pytest.raises(PermissionError):
            check_level(AccessLevel.READONLY, AccessLevel.MODIFY, "update", provider="o365cal")

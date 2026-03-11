# tests/test_levels.py
"""Tests for permission level system."""

import pytest

from ts4k.core.levels import AccessLevel, check_level, parse_level, scopes_for


class TestAccessLevel:
    def test_ordering(self):
        assert AccessLevel.READONLY < AccessLevel.MODIFY < AccessLevel.DRAFT < AccessLevel.SEND

    def test_values(self):
        assert AccessLevel.READONLY == 0
        assert AccessLevel.MODIFY == 1
        assert AccessLevel.DRAFT == 2
        assert AccessLevel.SEND == 3


class TestParseLevel:
    def test_none_defaults_to_readonly(self):
        assert parse_level(None) == AccessLevel.READONLY

    def test_parses_lowercase(self):
        assert parse_level("readonly") == AccessLevel.READONLY
        assert parse_level("modify") == AccessLevel.MODIFY
        assert parse_level("draft") == AccessLevel.DRAFT

    def test_parses_uppercase(self):
        assert parse_level("MODIFY") == AccessLevel.MODIFY

    def test_parses_mixed_case(self):
        assert parse_level("Draft") == AccessLevel.DRAFT

    def test_parses_send(self):
        assert parse_level("send") == AccessLevel.SEND

    def test_invalid_raises(self):
        with pytest.raises(KeyError):
            parse_level("admin")

    def test_empty_string_raises(self):
        with pytest.raises(KeyError):
            parse_level("")


class TestCheckLevel:
    def test_allows_equal(self):
        check_level(AccessLevel.MODIFY, AccessLevel.MODIFY, "archive")

    def test_allows_higher(self):
        check_level(AccessLevel.DRAFT, AccessLevel.MODIFY, "archive")

    def test_blocks_lower(self):
        with pytest.raises(PermissionError, match="archive"):
            check_level(AccessLevel.READONLY, AccessLevel.MODIFY, "archive")

    def test_error_message_includes_upgrade_hint(self):
        with pytest.raises(PermissionError, match="level=modify"):
            check_level(AccessLevel.READONLY, AccessLevel.MODIFY, "archive")

    def test_send_always_blocked(self):
        """Send level is defined but intentionally not implemented."""
        with pytest.raises(NotImplementedError, match="intentionally not implemented"):
            check_level(AccessLevel.SEND, AccessLevel.SEND, "send_message")

    def test_check_level_send_blocked_for_messaging(self):
        """SEND is blocked for non-gcal providers (the default)."""
        with pytest.raises(NotImplementedError, match="never sends messages"):
            check_level(AccessLevel.SEND, AccessLevel.SEND, "send_message")

    def test_check_level_send_allowed_for_gcal(self):
        """SEND is allowed when provider='gcal'."""
        check_level(AccessLevel.SEND, AccessLevel.SEND, "create_event", provider="gcal")

    def test_check_level_send_blocked_for_gmail(self):
        """SEND is still blocked for gmail even with explicit provider."""
        with pytest.raises(NotImplementedError):
            check_level(AccessLevel.SEND, AccessLevel.SEND, "send", provider="gmail")


class TestScopesFor:
    def test_gmail_readonly(self):
        scopes = scopes_for("gmail", AccessLevel.READONLY)
        assert "gmail.readonly" in scopes[0]

    def test_gmail_modify(self):
        scopes = scopes_for("gmail", AccessLevel.MODIFY)
        assert "gmail.modify" in scopes[0]

    def test_gmail_draft(self):
        scopes = scopes_for("gmail", AccessLevel.DRAFT)
        assert "gmail.modify" in scopes[0]

    def test_o365_readonly(self):
        scopes = scopes_for("o365", AccessLevel.READONLY)
        assert any("Mail.Read" in s for s in scopes)
        assert not any("ReadWrite" in s for s in scopes)

    def test_o365_modify(self):
        scopes = scopes_for("o365", AccessLevel.MODIFY)
        assert any("Mail.ReadWrite" in s for s in scopes)

    def test_o365_draft(self):
        scopes = scopes_for("o365", AccessLevel.DRAFT)
        assert any("Mail.ReadWrite" in s for s in scopes)

    def test_whatsapp_returns_empty(self):
        assert scopes_for("whatsapp", AccessLevel.MODIFY) == []

    def test_unknown_provider_returns_empty(self):
        assert scopes_for("telegram", AccessLevel.READONLY) == []

    def test_scopes_for_gcal_readonly(self):
        scopes = scopes_for("gcal", AccessLevel.READONLY)
        assert scopes == ["https://www.googleapis.com/auth/calendar.readonly"]

    def test_scopes_for_gcal_modify(self):
        scopes = scopes_for("gcal", AccessLevel.MODIFY)
        assert scopes == ["https://www.googleapis.com/auth/calendar"]

    def test_scopes_for_gcal_send(self):
        scopes = scopes_for("gcal", AccessLevel.SEND)
        assert scopes == ["https://www.googleapis.com/auth/calendar"]


class TestAdapterLevelPassthrough:
    def test_gmail_adapter_default_readonly(self):
        from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
        adapter = GmailAdapter(GmailAdapterConfig(user_email="a@b.com"))
        assert adapter.access_level == AccessLevel.READONLY

    def test_gmail_adapter_modify(self):
        from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
        adapter = GmailAdapter(GmailAdapterConfig(user_email="a@b.com", level="modify"))
        assert adapter.access_level == AccessLevel.MODIFY

    def test_o365_adapter_draft(self):
        from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig
        adapter = O365Adapter(O365AdapterConfig(client_id="x", level="draft"))
        assert adapter.access_level == AccessLevel.DRAFT

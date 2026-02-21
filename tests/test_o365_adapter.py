"""Tests for the O365 MCP client adapter.

Unit tests use realistic JSON fixtures matching Graph API output.
Parser tests are pure functions (no mocking needed).
Adapter tests mock the MCP ClientSession.call_tool.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ts4k.adapters.o365 import (
    O365Adapter,
    O365AdapterConfig,
    parse_list_response,
    parse_message_response,
)

# ---------------------------------------------------------------------------
# Realistic Graph API response fixtures
# ---------------------------------------------------------------------------

LIST_RESPONSE_JSON = """\
{
    "value": [
        {
            "id": "AAMkAGQ0Zjg0MDEzLWI2",
            "subject": "Q4 Budget Review",
            "from": {
                "emailAddress": {
                    "name": "Alice Chen",
                    "address": "alice@contoso.com"
                }
            },
            "receivedDateTime": "2026-02-20T14:30:00Z",
            "bodyPreview": "Hi Peter, please review the attached Q4 budget numbers before Friday.",
            "conversationId": "AAQkAGQ0Zjg0MDEzLWI2conv1",
            "hasAttachments": true,
            "internetMessageId": "<CABx+abc123@outlook.com>"
        },
        {
            "id": "AAMkAGQ0Zjg0MDEzLWI3",
            "subject": "Team Standup Notes",
            "from": {
                "emailAddress": {
                    "name": "Bob Martinez",
                    "address": "bob@contoso.com"
                }
            },
            "receivedDateTime": "2026-02-20T10:15:00Z",
            "bodyPreview": "Here are the notes from today's standup. Action items below.",
            "conversationId": "AAQkAGQ0Zjg0MDEzLWI3conv2",
            "hasAttachments": false,
            "internetMessageId": "<CABx+def456@outlook.com>"
        }
    ]
}"""

LIST_RESPONSE_EMPTY = '{"value": []}'

LIST_RESPONSE_BARE_ARRAY = """\
[
    {
        "id": "AAMkBareArray1",
        "subject": "Bare array message",
        "from": {
            "emailAddress": {
                "name": "Test User",
                "address": "test@example.com"
            }
        },
        "receivedDateTime": "2026-02-19T08:00:00Z",
        "bodyPreview": "This is a bare array response.",
        "conversationId": "conv-bare-1",
        "hasAttachments": false
    }
]"""

MESSAGE_RESPONSE_JSON = """\
{
    "id": "AAMkAGQ0Zjg0MDEzLWI2",
    "subject": "Q4 Budget Review",
    "from": {
        "emailAddress": {
            "name": "Alice Chen",
            "address": "alice@contoso.com"
        }
    },
    "receivedDateTime": "2026-02-20T14:30:00Z",
    "body": {
        "contentType": "html",
        "content": "<html><body><p>Hi Peter,</p><p>Please review the attached Q4 budget numbers before Friday.</p><p>Best,<br>Alice</p></body></html>"
    },
    "bodyPreview": "Hi Peter, please review the attached Q4 budget numbers before Friday.",
    "toRecipients": [
        {
            "emailAddress": {
                "name": "Test User",
                "address": "user@contoso.com"
            }
        }
    ],
    "ccRecipients": [
        {
            "emailAddress": {
                "name": "Finance Team",
                "address": "finance@contoso.com"
            }
        }
    ],
    "conversationId": "AAQkAGQ0Zjg0MDEzLWI2conv1",
    "hasAttachments": true,
    "internetMessageId": "<CABx+abc123@outlook.com>"
}"""

MESSAGE_RESPONSE_MINIMAL = """\
{
    "id": "AAMkMinimal1",
    "subject": "Quick ping",
    "from": {
        "emailAddress": {
            "address": "noreply@example.com"
        }
    },
    "receivedDateTime": "2026-02-19T16:00:00Z",
    "body": {
        "contentType": "text",
        "content": "Just checking in."
    },
    "conversationId": "conv-minimal-1"
}"""

THREAD_MESSAGES_JSON = """\
{
    "value": [
        {
            "id": "AAMkThread1Msg1",
            "subject": "Q4 Budget Review",
            "from": {
                "emailAddress": {
                    "name": "Alice Chen",
                    "address": "alice@contoso.com"
                }
            },
            "receivedDateTime": "2026-02-20T14:30:00Z",
            "body": {
                "contentType": "html",
                "content": "<p>Hi Peter, please review the Q4 budget.</p>"
            },
            "toRecipients": [
                {"emailAddress": {"name": "Peter", "address": "user@contoso.com"}}
            ],
            "conversationId": "AAQkConv1",
            "hasAttachments": true,
            "internetMessageId": "<thread-msg1@outlook.com>"
        },
        {
            "id": "AAMkThread1Msg2",
            "subject": "Re: Q4 Budget Review",
            "from": {
                "emailAddress": {
                    "name": "Test User",
                    "address": "user@contoso.com"
                }
            },
            "receivedDateTime": "2026-02-20T15:00:00Z",
            "body": {
                "contentType": "html",
                "content": "<p>Looks good, approved!</p>"
            },
            "toRecipients": [
                {"emailAddress": {"name": "Alice Chen", "address": "alice@contoso.com"}}
            ],
            "conversationId": "AAQkConv1",
            "hasAttachments": false,
            "internetMessageId": "<thread-msg2@outlook.com>"
        }
    ]
}"""

DISCOVER_RESPONSE_JSON = """\
{
    "mail": "user@contoso.com",
    "displayName": "Test User",
    "otherMails": ["user@example.com"],
    "proxyAddresses": [
        "SMTP:user@contoso.com",
        "smtp:hello@example.org",
        "smtp:support@example.org",
        "smtp:info@contoso.com"
    ]
}"""


# ---------------------------------------------------------------------------
# Parser unit tests (pure functions, no mocking needed)
# ---------------------------------------------------------------------------


class TestParseListResponse:
    """Tests for parse_list_response()."""

    def test_parses_multiple_entries(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert len(results) == 2

    def test_prefixes_ids(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert results[0]["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI2"
        assert results[1]["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI3"

    def test_preserves_raw_ids(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert results[0]["raw_id"] == "AAMkAGQ0Zjg0MDEzLWI2"

    def test_sets_source(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert results[0]["source"] == "oa"

    def test_prefixes_thread_ids(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert results[0]["thread_id"] == "oa:AAQkAGQ0Zjg0MDEzLWI2conv1"

    def test_formats_from_field(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert results[0]["from"] == "Alice Chen <alice@contoso.com>"
        assert results[1]["from"] == "Bob Martinez <bob@contoso.com>"

    def test_extracts_subject(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert results[0]["subject"] == "Q4 Budget Review"

    def test_extracts_date(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert results[0]["date"] == "2026-02-20T14:30:00Z"

    def test_extracts_body_preview(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert "Q4 budget numbers" in results[0]["body"]

    def test_extracts_has_attachments(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "oa")
        assert results[0]["has_attachments"] is True
        assert results[1]["has_attachments"] is False

    def test_empty_response(self):
        results = parse_list_response(LIST_RESPONSE_EMPTY, "oa")
        assert results == []

    def test_bare_array_response(self):
        results = parse_list_response(LIST_RESPONSE_BARE_ARRAY, "oa")
        assert len(results) == 1
        assert results[0]["id"] == "oa:AAMkBareArray1"

    def test_invalid_json_returns_empty(self):
        results = parse_list_response("not json at all", "oa")
        assert results == []

    def test_different_prefix(self):
        results = parse_list_response(LIST_RESPONSE_JSON, "ob")
        assert results[0]["id"].startswith("ob:")
        assert results[0]["thread_id"].startswith("ob:")
        assert results[0]["source"] == "ob"


class TestParseMessageResponse:
    """Tests for parse_message_response()."""

    def test_extracts_full_message(self):
        msg = parse_message_response(MESSAGE_RESPONSE_JSON, "oa")
        assert msg["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI2"
        assert msg["subject"] == "Q4 Budget Review"
        assert msg["from"] == "Alice Chen <alice@contoso.com>"
        assert msg["date"] == "2026-02-20T14:30:00Z"

    def test_extracts_html_body(self):
        msg = parse_message_response(MESSAGE_RESPONSE_JSON, "oa")
        assert "Q4 budget numbers" in msg["body"]

    def test_extracts_to_recipients(self):
        msg = parse_message_response(MESSAGE_RESPONSE_JSON, "oa")
        assert "Test User <user@contoso.com>" in msg["to"]

    def test_extracts_cc_recipients(self):
        msg = parse_message_response(MESSAGE_RESPONSE_JSON, "oa")
        assert "Finance Team <finance@contoso.com>" in msg["cc"]

    def test_extracts_internet_message_id(self):
        msg = parse_message_response(MESSAGE_RESPONSE_JSON, "oa")
        assert msg["message_id"] == "<CABx+abc123@outlook.com>"

    def test_extracts_thread_id(self):
        msg = parse_message_response(MESSAGE_RESPONSE_JSON, "oa")
        assert msg["thread_id"] == "oa:AAQkAGQ0Zjg0MDEzLWI2conv1"

    def test_has_attachments(self):
        msg = parse_message_response(MESSAGE_RESPONSE_JSON, "oa")
        assert msg["has_attachments"] is True

    def test_minimal_message(self):
        msg = parse_message_response(MESSAGE_RESPONSE_MINIMAL, "oa")
        assert msg["subject"] == "Quick ping"
        assert msg["from"] == "noreply@example.com"
        assert msg["body"] == "Just checking in."
        assert "to" not in msg
        assert "cc" not in msg
        assert "message_id" not in msg

    def test_invalid_json_returns_empty(self):
        msg = parse_message_response("not json", "oa")
        assert msg == {}

    def test_different_prefix(self):
        msg = parse_message_response(MESSAGE_RESPONSE_JSON, "oh")
        assert msg["id"].startswith("oh:")
        assert msg["source"] == "oh"


# ---------------------------------------------------------------------------
# Adapter-level tests (mock the MCP session)
# ---------------------------------------------------------------------------


def _make_text_content(text: str):
    """Create a mock TextContent object."""
    tc = MagicMock()
    tc.text = text
    tc.type = "text"
    return tc


def _make_call_result(text: str, is_error: bool = False):
    """Create a mock CallToolResult."""
    result = MagicMock()
    result.isError = is_error
    result.content = [_make_text_content(text)]
    return result


def _make_adapter(
    prefix: str = "oa",
    mailbox: str | None = "user@contoso.com",
) -> O365Adapter:
    """Create an O365Adapter with a mocked session."""
    config = O365AdapterConfig(mailbox=mailbox)
    adapter = O365Adapter(config, prefix=prefix)
    adapter._session = AsyncMock(spec=["call_tool", "initialize"])
    return adapter


class TestO365AdapterToolRouting:
    """Test that tool names and base args differ for shared vs personal mailbox."""

    def test_shared_mailbox_list_tool(self):
        adapter = _make_adapter(mailbox="hello@example.org")
        assert adapter._tool_name("list-mail-messages") == "list-shared-mailbox-messages"

    def test_shared_mailbox_get_tool(self):
        adapter = _make_adapter(mailbox="hello@example.org")
        assert adapter._tool_name("get-mail-message") == "get-shared-mailbox-message"

    def test_personal_mailbox_list_tool(self):
        adapter = _make_adapter(mailbox=None)
        assert adapter._tool_name("list-mail-messages") == "list-mail-messages"

    def test_personal_mailbox_get_tool(self):
        adapter = _make_adapter(mailbox=None)
        assert adapter._tool_name("get-mail-message") == "get-mail-message"

    def test_shared_mailbox_base_args(self):
        adapter = _make_adapter(mailbox="hello@example.org")
        assert adapter._base_args() == {"userId": "hello@example.org"}

    def test_personal_mailbox_base_args(self):
        adapter = _make_adapter(mailbox=None)
        assert adapter._base_args() == {}


class TestO365AdapterWhatsnew:
    """Test O365Adapter.whatsnew() with mocked MCP session."""

    @pytest.mark.asyncio
    async def test_returns_parsed_results(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(LIST_RESPONSE_JSON)
        )
        results = await adapter.whatsnew()
        assert len(results) == 2
        assert results[0]["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI2"

    @pytest.mark.asyncio
    async def test_uses_shared_mailbox_tool(self):
        adapter = _make_adapter(mailbox="hello@example.org")
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew(since="2026-02-20T00:00:00Z")

        call_args = adapter._session.call_tool.call_args
        assert call_args[0][0] == "list-shared-mailbox-messages"
        assert call_args[0][1]["userId"] == "hello@example.org"
        assert "$filter" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_uses_personal_tool_when_no_mailbox(self):
        adapter = _make_adapter(mailbox=None)
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew(since="2026-02-20T00:00:00Z")

        call_args = adapter._session.call_tool.call_args
        assert call_args[0][0] == "list-mail-messages"
        assert "userId" not in call_args[0][1]

    @pytest.mark.asyncio
    async def test_passes_filter_with_since(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew(since="2026-02-20T00:00:00Z")

        call_args = adapter._session.call_tool.call_args
        assert call_args[0][1]["$filter"] == "receivedDateTime ge 2026-02-20T00:00:00Z"

    @pytest.mark.asyncio
    async def test_default_since_is_1_day(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew()

        call_args = adapter._session.call_tool.call_args
        assert "receivedDateTime ge" in call_args[0][1]["$filter"]


class TestO365AdapterListMessages:
    """Test O365Adapter.list_messages() with mocked MCP session."""

    @pytest.mark.asyncio
    async def test_returns_parsed_results(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(LIST_RESPONSE_JSON)
        )
        results = await adapter.list_messages()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_passes_search_query(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages(query="budget review", count=10)

        call_args = adapter._session.call_tool.call_args
        assert call_args[0][1]["$search"] == '"budget review"'
        assert call_args[0][1]["$top"] == "10"

    @pytest.mark.asyncio
    async def test_no_search_without_query(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages()

        call_args = adapter._session.call_tool.call_args
        assert "$search" not in call_args[0][1]


class TestO365AdapterReadMessage:
    """Test O365Adapter.read_message() with mocked MCP session."""

    @pytest.mark.asyncio
    async def test_returns_parsed_message(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(MESSAGE_RESPONSE_JSON)
        )
        msg = await adapter.read_message("oa:AAMkAGQ0Zjg0MDEzLWI2")
        assert msg["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI2"
        assert msg["subject"] == "Q4 Budget Review"
        assert "Q4 budget numbers" in msg["body"]

    @pytest.mark.asyncio
    async def test_strips_prefix_for_upstream(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(MESSAGE_RESPONSE_MINIMAL)
        )
        await adapter.read_message("oa:AAMkMinimal1")

        call_args = adapter._session.call_tool.call_args
        assert call_args[0][1]["message-id"] == "AAMkMinimal1"

    @pytest.mark.asyncio
    async def test_uses_shared_get_tool(self):
        adapter = _make_adapter(mailbox="support@example.org")
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(MESSAGE_RESPONSE_MINIMAL)
        )
        await adapter.read_message("oa:AAMkMinimal1")

        call_args = adapter._session.call_tool.call_args
        assert call_args[0][0] == "get-shared-mailbox-message"
        assert call_args[0][1]["userId"] == "support@example.org"

    @pytest.mark.asyncio
    async def test_uses_personal_get_tool(self):
        adapter = _make_adapter(mailbox=None)
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(MESSAGE_RESPONSE_MINIMAL)
        )
        await adapter.read_message("oa:AAMkMinimal1")

        call_args = adapter._session.call_tool.call_args
        assert call_args[0][0] == "get-mail-message"


class TestO365AdapterReadThread:
    """Test O365Adapter.read_thread() with mocked MCP session."""

    @pytest.mark.asyncio
    async def test_returns_thread_structure(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(THREAD_MESSAGES_JSON)
        )
        thread = await adapter.read_thread("oa:AAQkConv1")

        assert thread["thread_id"] == "oa:AAQkConv1"
        assert thread["subject"] == "Q4 Budget Review"
        assert thread["message_count"] == 2
        assert len(thread["messages"]) == 2

    @pytest.mark.asyncio
    async def test_thread_messages_in_order(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(THREAD_MESSAGES_JSON)
        )
        thread = await adapter.read_thread("oa:AAQkConv1")

        assert "Alice Chen" in thread["messages"][0]["from"]
        assert "Test User" in thread["messages"][1]["from"]

    @pytest.mark.asyncio
    async def test_thread_uses_conversation_id_filter(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(THREAD_MESSAGES_JSON)
        )
        await adapter.read_thread("oa:AAQkConv1")

        call_args = adapter._session.call_tool.call_args
        assert "conversationId eq 'AAQkConv1'" in call_args[0][1]["$filter"]


class TestO365AdapterDiscoverMailboxes:
    """Test O365Adapter.discover_mailboxes()."""

    @pytest.mark.asyncio
    async def test_parses_discovery_response(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(DISCOVER_RESPONSE_JSON)
        )
        result = await adapter.discover_mailboxes()

        assert result["primary"] == "user@contoso.com"
        assert result["display_name"] == "Test User"
        assert "hello@example.org" in result["aliases"]
        assert "support@example.org" in result["aliases"]
        assert "info@contoso.com" in result["aliases"]
        # Primary should not appear in aliases
        assert "user@contoso.com" not in [a.lower() for a in result["aliases"]]


class TestO365AdapterErrorHandling:
    """Test error handling in the adapter."""

    @pytest.mark.asyncio
    async def test_upstream_error_raises(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result("Auth failed", is_error=True)
        )
        with pytest.raises(RuntimeError, match="returned error"):
            await adapter.list_messages()

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        config = O365AdapterConfig(mailbox="test@example.com")
        adapter = O365Adapter(config)
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.list_messages()

    @pytest.mark.asyncio
    async def test_empty_content_raises(self):
        adapter = _make_adapter()
        result = MagicMock()
        result.isError = False
        result.content = []
        adapter._session.call_tool = AsyncMock(return_value=result)

        with pytest.raises(RuntimeError, match="no text content"):
            await adapter.list_messages()


class TestO365AdapterSourcePrefix:
    """Test that source_prefix is correct."""

    def test_default_prefix(self):
        config = O365AdapterConfig()
        adapter = O365Adapter(config)
        assert adapter.source_prefix == "o"

    def test_custom_prefix(self):
        config = O365AdapterConfig()
        adapter = O365Adapter(config, prefix="oa")
        assert adapter.source_prefix == "oa"


class TestO365AdapterIdStripping:
    """Test the _strip_prefix helper."""

    def test_strips_prefix(self):
        adapter = _make_adapter(prefix="oa")
        assert adapter._strip_prefix("oa:AAMkAbc123") == "AAMkAbc123"

    def test_leaves_unprefixed(self):
        adapter = _make_adapter(prefix="oa")
        assert adapter._strip_prefix("AAMkAbc123") == "AAMkAbc123"

    def test_leaves_other_prefix(self):
        adapter = _make_adapter(prefix="oa")
        assert adapter._strip_prefix("ob:AAMkAbc123") == "ob:AAMkAbc123"

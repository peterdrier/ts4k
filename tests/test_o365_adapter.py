"""Tests for the O365 direct Graph API adapter.

Unit tests use realistic JSON fixtures matching Graph API output.
Converter tests are pure functions (no mocking needed).
Adapter tests mock the httpx.AsyncClient.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import httpx
import pytest

from ts4k.adapters.o365 import (
    O365Adapter,
    O365AdapterConfig,
    _list_response_to_dicts,
    _message_to_dict,
    _strip_prefix,
)

# ---------------------------------------------------------------------------
# Realistic Graph API response fixtures (as dicts)
# ---------------------------------------------------------------------------

LIST_RESPONSE_DATA = {
    "value": [
        {
            "id": "AAMkAGQ0Zjg0MDEzLWI2",
            "subject": "Q4 Budget Review",
            "from": {
                "emailAddress": {
                    "name": "Alice Chen",
                    "address": "alice@contoso.com",
                }
            },
            "receivedDateTime": "2026-02-20T14:30:00Z",
            "bodyPreview": "Hi Peter, please review the attached Q4 budget numbers before Friday.",
            "conversationId": "AAQkAGQ0Zjg0MDEzLWI2conv1",
            "hasAttachments": True,
            "internetMessageId": "<CABx+abc123@outlook.com>",
        },
        {
            "id": "AAMkAGQ0Zjg0MDEzLWI3",
            "subject": "Team Standup Notes",
            "from": {
                "emailAddress": {
                    "name": "Bob Martinez",
                    "address": "bob@contoso.com",
                }
            },
            "receivedDateTime": "2026-02-20T10:15:00Z",
            "bodyPreview": "Here are the notes from today's standup. Action items below.",
            "conversationId": "AAQkAGQ0Zjg0MDEzLWI3conv2",
            "hasAttachments": False,
            "internetMessageId": "<CABx+def456@outlook.com>",
        },
    ]
}

LIST_RESPONSE_EMPTY = {"value": []}

LIST_RESPONSE_BARE_ARRAY = [
    {
        "id": "AAMkBareArray1",
        "subject": "Bare array message",
        "from": {
            "emailAddress": {
                "name": "Test User",
                "address": "test@example.com",
            }
        },
        "receivedDateTime": "2026-02-19T08:00:00Z",
        "bodyPreview": "This is a bare array response.",
        "conversationId": "conv-bare-1",
        "hasAttachments": False,
    }
]

MESSAGE_RESPONSE_DATA = {
    "id": "AAMkAGQ0Zjg0MDEzLWI2",
    "subject": "Q4 Budget Review",
    "from": {
        "emailAddress": {
            "name": "Alice Chen",
            "address": "alice@contoso.com",
        }
    },
    "receivedDateTime": "2026-02-20T14:30:00Z",
    "body": {
        "contentType": "html",
        "content": "<html><body><p>Hi Peter,</p><p>Please review the attached Q4 budget numbers before Friday.</p><p>Best,<br>Alice</p></body></html>",
    },
    "bodyPreview": "Hi Peter, please review the attached Q4 budget numbers before Friday.",
    "toRecipients": [
        {
            "emailAddress": {
                "name": "Test User",
                "address": "user@contoso.com",
            }
        }
    ],
    "ccRecipients": [
        {
            "emailAddress": {
                "name": "Finance Team",
                "address": "finance@contoso.com",
            }
        }
    ],
    "conversationId": "AAQkAGQ0Zjg0MDEzLWI2conv1",
    "hasAttachments": True,
    "internetMessageId": "<CABx+abc123@outlook.com>",
}

MESSAGE_RESPONSE_MINIMAL = {
    "id": "AAMkMinimal1",
    "subject": "Quick ping",
    "from": {
        "emailAddress": {
            "address": "noreply@example.com",
        }
    },
    "receivedDateTime": "2026-02-19T16:00:00Z",
    "body": {
        "contentType": "text",
        "content": "Just checking in.",
    },
    "conversationId": "conv-minimal-1",
}

THREAD_MESSAGES_DATA = {
    "value": [
        {
            "id": "AAMkThread1Msg1",
            "subject": "Q4 Budget Review",
            "from": {
                "emailAddress": {
                    "name": "Alice Chen",
                    "address": "alice@contoso.com",
                }
            },
            "receivedDateTime": "2026-02-20T14:30:00Z",
            "body": {
                "contentType": "html",
                "content": "<p>Hi Peter, please review the Q4 budget.</p>",
            },
            "toRecipients": [
                {"emailAddress": {"name": "Peter", "address": "user@contoso.com"}}
            ],
            "conversationId": "AAQkConv1",
            "hasAttachments": True,
            "internetMessageId": "<thread-msg1@outlook.com>",
        },
        {
            "id": "AAMkThread1Msg2",
            "subject": "Re: Q4 Budget Review",
            "from": {
                "emailAddress": {
                    "name": "Test User",
                    "address": "user@contoso.com",
                }
            },
            "receivedDateTime": "2026-02-20T15:00:00Z",
            "body": {
                "contentType": "html",
                "content": "<p>Looks good, approved!</p>",
            },
            "toRecipients": [
                {"emailAddress": {"name": "Alice Chen", "address": "alice@contoso.com"}}
            ],
            "conversationId": "AAQkConv1",
            "hasAttachments": False,
            "internetMessageId": "<thread-msg2@outlook.com>",
        },
    ]
}

DISCOVER_RESPONSE_DATA = {
    "mail": "user@contoso.com",
    "displayName": "Test User",
    "otherMails": ["user@example.com"],
    "proxyAddresses": [
        "SMTP:user@contoso.com",
        "smtp:hello@example.org",
        "smtp:support@example.org",
        "smtp:info@contoso.com",
    ],
}


# ---------------------------------------------------------------------------
# Converter unit tests (pure functions, no mocking needed)
# ---------------------------------------------------------------------------


class TestListResponseToDicts:
    """Tests for _list_response_to_dicts()."""

    def test_parses_multiple_entries(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert len(results) == 2

    def test_prefixes_ids(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert results[0]["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI2"
        assert results[1]["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI3"

    def test_preserves_raw_ids(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert results[0]["raw_id"] == "AAMkAGQ0Zjg0MDEzLWI2"

    def test_sets_source(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert results[0]["source"] == "oa"

    def test_prefixes_thread_ids(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert results[0]["thread_id"] == "oa:AAQkAGQ0Zjg0MDEzLWI2conv1"

    def test_formats_from_field(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert results[0]["from"] == "Alice Chen <alice@contoso.com>"
        assert results[1]["from"] == "Bob Martinez <bob@contoso.com>"

    def test_extracts_subject(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert results[0]["subject"] == "Q4 Budget Review"

    def test_extracts_date(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert results[0]["date"] == "2026-02-20T14:30:00Z"

    def test_extracts_body_preview(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert "Q4 budget numbers" in results[0]["body"]

    def test_extracts_has_attachments(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "oa")
        assert results[0]["has_attachments"] is True
        assert results[1]["has_attachments"] is False

    def test_empty_response(self):
        results = _list_response_to_dicts(LIST_RESPONSE_EMPTY, "oa")
        assert results == []

    def test_bare_array_response(self):
        results = _list_response_to_dicts(LIST_RESPONSE_BARE_ARRAY, "oa")
        assert len(results) == 1
        assert results[0]["id"] == "oa:AAMkBareArray1"

    def test_different_prefix(self):
        results = _list_response_to_dicts(LIST_RESPONSE_DATA, "ob")
        assert results[0]["id"].startswith("ob:")
        assert results[0]["thread_id"].startswith("ob:")
        assert results[0]["source"] == "ob"


class TestMessageToDict:
    """Tests for _message_to_dict()."""

    def test_extracts_full_message(self):
        msg = _message_to_dict(MESSAGE_RESPONSE_DATA, "oa")
        assert msg["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI2"
        assert msg["subject"] == "Q4 Budget Review"
        assert msg["from"] == "Alice Chen <alice@contoso.com>"
        assert msg["date"] == "2026-02-20T14:30:00Z"

    def test_extracts_html_body(self):
        msg = _message_to_dict(MESSAGE_RESPONSE_DATA, "oa")
        assert "Q4 budget numbers" in msg["body"]

    def test_extracts_to_recipients(self):
        msg = _message_to_dict(MESSAGE_RESPONSE_DATA, "oa")
        assert "Test User <user@contoso.com>" in msg["to"]

    def test_extracts_cc_recipients(self):
        msg = _message_to_dict(MESSAGE_RESPONSE_DATA, "oa")
        assert "Finance Team <finance@contoso.com>" in msg["cc"]

    def test_extracts_internet_message_id(self):
        msg = _message_to_dict(MESSAGE_RESPONSE_DATA, "oa")
        assert msg["message_id"] == "<CABx+abc123@outlook.com>"

    def test_extracts_thread_id(self):
        msg = _message_to_dict(MESSAGE_RESPONSE_DATA, "oa")
        assert msg["thread_id"] == "oa:AAQkAGQ0Zjg0MDEzLWI2conv1"

    def test_has_attachments(self):
        msg = _message_to_dict(MESSAGE_RESPONSE_DATA, "oa")
        assert msg["has_attachments"] is True

    def test_minimal_message(self):
        msg = _message_to_dict(MESSAGE_RESPONSE_MINIMAL, "oa")
        assert msg["subject"] == "Quick ping"
        assert msg["from"] == "noreply@example.com"
        assert msg["body"] == "Just checking in."
        assert "to" not in msg
        assert "cc" not in msg
        assert "message_id" not in msg

    def test_empty_dict_returns_empty(self):
        msg = _message_to_dict({}, "oa")
        assert msg == {}

    def test_different_prefix(self):
        msg = _message_to_dict(MESSAGE_RESPONSE_DATA, "oh")
        assert msg["id"].startswith("oh:")
        assert msg["source"] == "oh"


# ---------------------------------------------------------------------------
# Adapter-level tests (mock the httpx client)
# ---------------------------------------------------------------------------


def _mock_response(data: dict | list, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response with JSON data."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
    return resp


def _make_adapter(
    prefix: str = "oa",
    mailbox: str | None = "user@contoso.com",
) -> O365Adapter:
    """Create an O365Adapter with a mocked httpx client."""
    config = O365AdapterConfig(client_id="test-client", mailbox=mailbox)
    adapter = O365Adapter(config, prefix=prefix)
    adapter._client = AsyncMock(spec=httpx.AsyncClient)
    return adapter


class TestO365AdapterUrlRouting:
    """Test that URL base paths differ for shared vs personal mailbox."""

    def test_shared_mailbox_url(self):
        adapter = _make_adapter(mailbox="hello@example.org")
        assert adapter._base_url() == "/users/hello@example.org"

    def test_personal_mailbox_url(self):
        adapter = _make_adapter(mailbox=None)
        assert adapter._base_url() == "/me"


class TestO365AdapterWhatsnew:
    """Test O365Adapter.whatsnew() with mocked httpx client."""

    @pytest.mark.asyncio
    async def test_returns_parsed_results(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_DATA)
        )
        results = await adapter.whatsnew()
        assert len(results) == 2
        assert results[0]["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI2"

    @pytest.mark.asyncio
    async def test_uses_shared_mailbox_url(self):
        adapter = _make_adapter(mailbox="hello@example.org")
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew(since="2026-02-20T00:00:00Z")

        call_args = adapter._client.get.call_args
        assert call_args[0][0] == "/users/hello@example.org/messages"
        assert "$filter" in call_args[1]["params"]

    @pytest.mark.asyncio
    async def test_uses_personal_url_when_no_mailbox(self):
        adapter = _make_adapter(mailbox=None)
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew(since="2026-02-20T00:00:00Z")

        call_args = adapter._client.get.call_args
        assert call_args[0][0] == "/me/messages"

    @pytest.mark.asyncio
    async def test_passes_filter_with_since(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew(since="2026-02-20T00:00:00Z")

        call_args = adapter._client.get.call_args
        assert call_args[1]["params"]["$filter"] == "receivedDateTime ge 2026-02-20T00:00:00Z"

    @pytest.mark.asyncio
    async def test_default_since_is_1_day(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew()

        call_args = adapter._client.get.call_args
        assert "receivedDateTime ge" in call_args[1]["params"]["$filter"]


class TestO365AdapterListMessages:
    """Test O365Adapter.list_messages() with mocked httpx client."""

    @pytest.mark.asyncio
    async def test_returns_parsed_results(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_DATA)
        )
        results = await adapter.list_messages()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_passes_search_query(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages(query="budget review", count=10)

        call_args = adapter._client.get.call_args
        assert call_args[1]["params"]["$search"] == '"budget review"'
        assert call_args[1]["params"]["$top"] == "10"

    @pytest.mark.asyncio
    async def test_no_search_without_query(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages()

        call_args = adapter._client.get.call_args
        assert "$search" not in call_args[1]["params"]

    @pytest.mark.asyncio
    async def test_search_adds_consistency_header(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages(query="budget review", count=10)

        call_args = adapter._client.get.call_args
        assert call_args[1].get("headers", {}).get("ConsistencyLevel") == "eventual"

    @pytest.mark.asyncio
    async def test_search_removes_orderby(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages(query="budget review", count=10)

        call_args = adapter._client.get.call_args
        assert "$orderby" not in call_args[1]["params"]

    @pytest.mark.asyncio
    async def test_no_search_keeps_orderby(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages(count=10)

        call_args = adapter._client.get.call_args
        assert "$orderby" in call_args[1]["params"]

    @pytest.mark.asyncio
    async def test_no_search_no_consistency_header(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages(count=10)

        call_args = adapter._client.get.call_args
        assert "headers" not in call_args[1] or "ConsistencyLevel" not in call_args[1].get("headers", {})


class TestO365AdapterReadMessage:
    """Test O365Adapter.read_message() with mocked httpx client."""

    @pytest.mark.asyncio
    async def test_returns_parsed_message(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(MESSAGE_RESPONSE_DATA)
        )
        msg = await adapter.read_message("oa:AAMkAGQ0Zjg0MDEzLWI2")
        assert msg["id"] == "oa:AAMkAGQ0Zjg0MDEzLWI2"
        assert msg["subject"] == "Q4 Budget Review"
        assert "Q4 budget numbers" in msg["body"]

    @pytest.mark.asyncio
    async def test_strips_prefix_for_upstream(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(MESSAGE_RESPONSE_MINIMAL)
        )
        await adapter.read_message("oa:AAMkMinimal1")

        call_args = adapter._client.get.call_args
        assert "/messages/AAMkMinimal1" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_uses_shared_mailbox_url(self):
        adapter = _make_adapter(mailbox="support@example.org")
        adapter._client.get = AsyncMock(
            return_value=_mock_response(MESSAGE_RESPONSE_MINIMAL)
        )
        await adapter.read_message("oa:AAMkMinimal1")

        call_args = adapter._client.get.call_args
        assert call_args[0][0] == "/users/support@example.org/messages/AAMkMinimal1"

    @pytest.mark.asyncio
    async def test_uses_personal_url(self):
        adapter = _make_adapter(mailbox=None)
        adapter._client.get = AsyncMock(
            return_value=_mock_response(MESSAGE_RESPONSE_MINIMAL)
        )
        await adapter.read_message("oa:AAMkMinimal1")

        call_args = adapter._client.get.call_args
        assert call_args[0][0] == "/me/messages/AAMkMinimal1"


class TestO365AdapterReadThread:
    """Test O365Adapter.read_thread() with mocked httpx client."""

    @pytest.mark.asyncio
    async def test_returns_thread_structure(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(THREAD_MESSAGES_DATA)
        )
        thread = await adapter.read_thread("oa:AAQkConv1")

        assert thread["thread_id"] == "oa:AAQkConv1"
        assert thread["subject"] == "Q4 Budget Review"
        assert thread["message_count"] == 2
        assert len(thread["messages"]) == 2

    @pytest.mark.asyncio
    async def test_thread_messages_in_order(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(THREAD_MESSAGES_DATA)
        )
        thread = await adapter.read_thread("oa:AAQkConv1")

        assert "Alice Chen" in thread["messages"][0]["from"]
        assert "Test User" in thread["messages"][1]["from"]

    @pytest.mark.asyncio
    async def test_thread_uses_conversation_id_filter(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(THREAD_MESSAGES_DATA)
        )
        await adapter.read_thread("oa:AAQkConv1")

        call_args = adapter._client.get.call_args
        assert "conversationId eq 'AAQkConv1'" in call_args[1]["params"]["$filter"]


class TestO365AdapterDiscoverMailboxes:
    """Test O365Adapter.discover_mailboxes()."""

    @pytest.mark.asyncio
    async def test_parses_discovery_response(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(DISCOVER_RESPONSE_DATA)
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
    async def test_http_error_raises(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response({}, status_code=401)
        )
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.list_messages()

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        config = O365AdapterConfig(client_id="test", mailbox="test@example.com")
        adapter = O365Adapter(config)
        with pytest.raises(RuntimeError, match="not connected"):
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


class TestO365AdapterSenderFilter:
    """Test sender/domain filtering on O365 adapter."""

    @pytest.mark.asyncio
    async def test_sender_filter_on_whatsnew(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew(since="2026-01-01T00:00:00Z", sender="alice@contoso.com")

        call_args = adapter._client.get.call_args
        filt = call_args[1]["params"]["$filter"]
        assert "alice@contoso.com" in filt
        assert "receivedDateTime ge" in filt

    @pytest.mark.asyncio
    async def test_domain_filter_on_whatsnew(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.whatsnew(since="2026-01-01T00:00:00Z", domain="contoso.com")

        call_args = adapter._client.get.call_args
        params = call_args[1]["params"]
        # Domain uses $search alone (can't combine with $filter on messages)
        assert "contoso.com" in params["$search"]
        assert "$filter" not in params
        assert "$orderby" not in params
        assert call_args[1].get("headers", {}).get("ConsistencyLevel") == "eventual"

    @pytest.mark.asyncio
    async def test_sender_filter_on_list(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages(sender="alice@contoso.com")

        call_args = adapter._client.get.call_args
        filt = call_args[1]["params"]["$filter"]
        assert "alice@contoso.com" in filt

    @pytest.mark.asyncio
    async def test_domain_filter_on_list(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages(domain="contoso.com")

        call_args = adapter._client.get.call_args
        params = call_args[1]["params"]
        # Domain uses $search, not $filter
        assert "contoso.com" in params["$search"]
        assert "$filter" not in params
        assert "$orderby" not in params

    @pytest.mark.asyncio
    async def test_sender_filter_with_query(self):
        """Sender filter + free-text query should both be present."""
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(LIST_RESPONSE_EMPTY)
        )
        await adapter.list_messages(query="budget", sender="alice@contoso.com")

        call_args = adapter._client.get.call_args
        params = call_args[1]["params"]
        assert "$search" in params
        assert "$filter" in params
        assert "alice@contoso.com" in params["$filter"]


class TestStripPrefix:
    """Test the _strip_prefix helper."""

    def test_strips_prefix(self):
        assert _strip_prefix("oa:AAMkAbc123", "oa") == "AAMkAbc123"

    def test_leaves_unprefixed(self):
        assert _strip_prefix("AAMkAbc123", "oa") == "AAMkAbc123"

    def test_leaves_other_prefix(self):
        assert _strip_prefix("ob:AAMkAbc123", "oa") == "ob:AAMkAbc123"

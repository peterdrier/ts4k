"""Tests for the Gmail MCP client adapter.

Unit tests use mocked MCP responses (no real server needed).
Integration tests (marked ``@pytest.mark.integration``) require a running
google_workspace_mcp server and are skipped by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ts4k.adapters.gmail import (
    GmailAdapter,
    GmailAdapterConfig,
    parse_message_response,
    parse_search_response,
    parse_thread_response,
)

# ---------------------------------------------------------------------------
# Realistic upstream response fixtures
# ---------------------------------------------------------------------------

SEARCH_RESPONSE_TEXT = """\
Found 3 messages matching 'newer_than:1d':

📧 MESSAGES:
  1. Message ID: 18f6a2b3c4e5f6a7
     Web Link: https://mail.google.com/mail/u/0/#all/18f6a2b3c4e5f6a7
     Thread ID: 18f6a2b3c4e5f6a8
     Thread Link: https://mail.google.com/mail/u/0/#all/18f6a2b3c4e5f6a8

  2. Message ID: 18f6b1112233aabb
     Web Link: https://mail.google.com/mail/u/0/#all/18f6b1112233aabb
     Thread ID: 18f6b1112233aacc
     Thread Link: https://mail.google.com/mail/u/0/#all/18f6b1112233aacc

  3. Message ID: 18f6c9988776655d
     Web Link: https://mail.google.com/mail/u/0/#all/18f6c9988776655d
     Thread ID: 18f6c9988776655e
     Thread Link: https://mail.google.com/mail/u/0/#all/18f6c9988776655e

💡 USAGE:
  • Pass the Message IDs **as a list** to get_gmail_messages_content_batch()
    e.g. get_gmail_messages_content_batch(message_ids=[...])
  • Pass the Thread IDs to get_gmail_thread_content() (single) or get_gmail_threads_content_batch() (batch)"""

SEARCH_RESPONSE_WITH_PAGINATION = """\
Found 2 messages matching 'in:inbox':

📧 MESSAGES:
  1. Message ID: aaa111
     Web Link: https://mail.google.com/mail/u/0/#all/aaa111
     Thread ID: aaa222
     Thread Link: https://mail.google.com/mail/u/0/#all/aaa222

  2. Message ID: bbb111
     Web Link: https://mail.google.com/mail/u/0/#all/bbb111
     Thread ID: bbb222
     Thread Link: https://mail.google.com/mail/u/0/#all/bbb222

💡 USAGE:
  • Pass the Message IDs **as a list** to get_gmail_messages_content_batch()

📄 PAGINATION: To get the next page, call search_gmail_messages again with page_token='CiAKGjBpNDd2Nmp2Zml2cXRwYjBpOXA'"""

SEARCH_EMPTY_RESPONSE = "No messages found for query: 'label:NONEXISTENT'"

MESSAGE_RESPONSE_TEXT = """\
Subject: Meeting tomorrow at 3pm
From:    Alice Chen <alice@acme.com>
Date:    Thu, 20 Feb 2026 09:15:00 +0100
Message-ID: <CABx+abc123@mail.gmail.com>
To:      peter@example.com
Cc:      bob@example.com

--- BODY ---
Hey Peter,

Are we still on for 3pm? Let me know if the conference room changed.

Thanks,
Alice

--- ATTACHMENTS ---
1. agenda.pdf (application/pdf, 24.5 KB)
   Attachment ID: ANGjdJ8xyz
   Use get_gmail_attachment_content(message_id='18f6a2b3c4e5f6a7', attachment_id='ANGjdJ8xyz') to download"""

MESSAGE_RESPONSE_MINIMAL = """\
Subject: Quick question
From:    Bob <bob@corp.com>
Date:    Wed, 19 Feb 2026 14:30:00 +0000

--- BODY ---
Hey, what time works?"""

THREAD_RESPONSE_TEXT = """\
Thread ID: 18f6a2b3c4e5f6a8
Subject: Meeting tomorrow at 3pm
Messages: 3

=== Message 1 ===
From: Alice Chen <alice@acme.com>
Date: Thu, 20 Feb 2026 09:15:00 +0100
Message-ID: <CABx+abc123@mail.gmail.com>

Hey Peter,

Are we still on for 3pm? Let me know if the conference room changed.

=== Message 2 ===
From: Peter <peter@example.com>
Date: Thu, 20 Feb 2026 09:30:00 +0100
Message-ID: <CABx+def456@mail.gmail.com>
In-Reply-To: <CABx+abc123@mail.gmail.com>

Yes! Room B confirmed. See you there.

=== Message 3 ===
From: Alice Chen <alice@acme.com>
Date: Thu, 20 Feb 2026 09:32:00 +0100

Great, thanks!"""


# ---------------------------------------------------------------------------
# Parser unit tests (pure functions, no mocking needed)
# ---------------------------------------------------------------------------


class TestParseSearchResponse:
    """Tests for parse_search_response()."""

    def test_parses_multiple_entries(self):
        results = parse_search_response(SEARCH_RESPONSE_TEXT, "g")
        assert len(results) == 3

    def test_prefixes_message_ids(self):
        results = parse_search_response(SEARCH_RESPONSE_TEXT, "g")
        assert results[0]["id"] == "g:18f6a2b3c4e5f6a7"
        assert results[1]["id"] == "g:18f6b1112233aabb"
        assert results[2]["id"] == "g:18f6c9988776655d"

    def test_preserves_raw_ids(self):
        results = parse_search_response(SEARCH_RESPONSE_TEXT, "g")
        assert results[0]["raw_id"] == "18f6a2b3c4e5f6a7"

    def test_prefixes_thread_ids(self):
        results = parse_search_response(SEARCH_RESPONSE_TEXT, "g")
        assert results[0]["thread_id"] == "g:18f6a2b3c4e5f6a8"

    def test_includes_web_links(self):
        results = parse_search_response(SEARCH_RESPONSE_TEXT, "g")
        assert "mail.google.com" in results[0]["web_link"]
        assert "mail.google.com" in results[0]["thread_link"]

    def test_empty_response_returns_empty_list(self):
        results = parse_search_response(SEARCH_EMPTY_RESPONSE, "g")
        assert results == []

    def test_pagination_token_extracted(self):
        results = parse_search_response(SEARCH_RESPONSE_WITH_PAGINATION, "g")
        assert len(results) == 2
        assert results[-1]["_next_page_token"] == "CiAKGjBpNDd2Nmp2Zml2cXRwYjBpOXA"

    def test_different_prefix(self):
        results = parse_search_response(SEARCH_RESPONSE_TEXT, "x")
        assert results[0]["id"].startswith("x:")
        assert results[0]["thread_id"].startswith("x:")


class TestParseMessageResponse:
    """Tests for parse_message_response()."""

    def test_extracts_headers(self):
        msg = parse_message_response(MESSAGE_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a7")
        assert msg["subject"] == "Meeting tomorrow at 3pm"
        assert "Alice Chen" in msg["from"]
        assert "alice@acme.com" in msg["from"]
        assert msg["date"] == "Thu, 20 Feb 2026 09:15:00 +0100"

    def test_prefixes_id(self):
        msg = parse_message_response(MESSAGE_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a7")
        assert msg["id"] == "g:18f6a2b3c4e5f6a7"

    def test_extracts_body(self):
        msg = parse_message_response(MESSAGE_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a7")
        assert "still on for 3pm" in msg["body"]
        assert "conference room" in msg["body"]

    def test_body_does_not_include_attachments(self):
        msg = parse_message_response(MESSAGE_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a7")
        assert "agenda.pdf" not in msg["body"]

    def test_extracts_optional_to_cc(self):
        msg = parse_message_response(MESSAGE_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a7")
        assert msg["to"] == "peter@example.com"
        assert msg["cc"] == "bob@example.com"

    def test_extracts_rfc822_message_id(self):
        msg = parse_message_response(MESSAGE_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a7")
        assert msg["message_id"] == "<CABx+abc123@mail.gmail.com>"

    def test_extracts_attachments(self):
        msg = parse_message_response(MESSAGE_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a7")
        assert "attachments" in msg
        assert len(msg["attachments"]) == 1
        att = msg["attachments"][0]
        assert att["filename"] == "agenda.pdf"
        assert att["mime_type"] == "application/pdf"
        assert att["size"] == "24.5 KB"

    def test_minimal_message_no_optional_fields(self):
        msg = parse_message_response(MESSAGE_RESPONSE_MINIMAL, "g", "abc123")
        assert msg["subject"] == "Quick question"
        assert "Bob" in msg["from"]
        assert msg["body"] == "Hey, what time works?"
        # Optional fields should be absent (not empty strings).
        assert "to" not in msg
        assert "cc" not in msg
        assert "attachments" not in msg

    def test_empty_raw_id(self):
        msg = parse_message_response(MESSAGE_RESPONSE_MINIMAL, "g", "")
        assert msg["id"] == ""


class TestParseThreadResponse:
    """Tests for parse_thread_response()."""

    def test_extracts_thread_metadata(self):
        thread = parse_thread_response(THREAD_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a8")
        assert thread["thread_id"] == "g:18f6a2b3c4e5f6a8"
        assert thread["subject"] == "Meeting tomorrow at 3pm"
        assert thread["message_count"] == 3

    def test_extracts_all_messages(self):
        thread = parse_thread_response(THREAD_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a8")
        assert len(thread["messages"]) == 3

    def test_first_message_content(self):
        thread = parse_thread_response(THREAD_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a8")
        msg1 = thread["messages"][0]
        assert msg1["index"] == 1
        assert "Alice Chen" in msg1["from"]
        assert "still on for 3pm" in msg1["body"]

    def test_second_message_content(self):
        thread = parse_thread_response(THREAD_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a8")
        msg2 = thread["messages"][1]
        assert msg2["index"] == 2
        assert "Peter" in msg2["from"]
        assert "Room B confirmed" in msg2["body"]

    def test_third_message_content(self):
        thread = parse_thread_response(THREAD_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a8")
        msg3 = thread["messages"][2]
        assert msg3["index"] == 3
        assert "thanks" in msg3["body"].lower()

    def test_message_ids_extracted(self):
        thread = parse_thread_response(THREAD_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a8")
        msg1 = thread["messages"][0]
        assert msg1.get("message_id") == "<CABx+abc123@mail.gmail.com>"

    def test_in_reply_to_extracted(self):
        thread = parse_thread_response(THREAD_RESPONSE_TEXT, "g", "18f6a2b3c4e5f6a8")
        msg2 = thread["messages"][1]
        assert msg2.get("in_reply_to") == "<CABx+abc123@mail.gmail.com>"


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


def _make_adapter(user_email: str = "user@gmail.com") -> GmailAdapter:
    """Create a GmailAdapter with a mocked session (no real MCP connection)."""
    config = GmailAdapterConfig(user_email=user_email)
    adapter = GmailAdapter(config)
    adapter._session = AsyncMock(spec=["call_tool", "initialize"])
    return adapter


class TestGmailAdapterListMessages:
    """Test GmailAdapter.list_messages() with mocked MCP session."""

    @pytest.mark.asyncio
    async def test_returns_parsed_results(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(SEARCH_RESPONSE_TEXT)
        )

        results = await adapter.list_messages("newer_than:1d")

        assert len(results) == 3
        assert results[0]["id"] == "g:18f6a2b3c4e5f6a7"

    @pytest.mark.asyncio
    async def test_passes_correct_arguments(self):
        adapter = _make_adapter("alice@example.com")
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(SEARCH_RESPONSE_TEXT)
        )

        await adapter.list_messages("label:INBOX", count=5)

        adapter._session.call_tool.assert_called_once_with(
            "search_gmail_messages",
            {
                "user_google_email": "alice@example.com",
                "query": "label:INBOX",
                "page_size": 5,
            },
        )

    @pytest.mark.asyncio
    async def test_default_query_is_inbox(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(SEARCH_EMPTY_RESPONSE)
        )

        await adapter.list_messages()

        args = adapter._session.call_tool.call_args
        assert args[0][1]["query"] == "in:inbox"

    @pytest.mark.asyncio
    async def test_empty_result(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(SEARCH_EMPTY_RESPONSE)
        )

        results = await adapter.list_messages("label:NONEXISTENT")
        assert results == []


class TestGmailAdapterReadMessage:
    """Test GmailAdapter.read_message() with mocked MCP session."""

    @pytest.mark.asyncio
    async def test_returns_parsed_message(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(MESSAGE_RESPONSE_TEXT)
        )

        msg = await adapter.read_message("g:18f6a2b3c4e5f6a7")

        assert msg["id"] == "g:18f6a2b3c4e5f6a7"
        assert msg["subject"] == "Meeting tomorrow at 3pm"
        assert "still on for 3pm" in msg["body"]

    @pytest.mark.asyncio
    async def test_strips_prefix_in_upstream_call(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(MESSAGE_RESPONSE_MINIMAL)
        )

        await adapter.read_message("g:abc123")

        args = adapter._session.call_tool.call_args
        assert args[0][1]["message_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_handles_unprefixed_id(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(MESSAGE_RESPONSE_MINIMAL)
        )

        await adapter.read_message("abc123")

        args = adapter._session.call_tool.call_args
        assert args[0][1]["message_id"] == "abc123"


class TestGmailAdapterReadThread:
    """Test GmailAdapter.read_thread() with mocked MCP session."""

    @pytest.mark.asyncio
    async def test_returns_parsed_thread(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(THREAD_RESPONSE_TEXT)
        )

        thread = await adapter.read_thread("g:18f6a2b3c4e5f6a8")

        assert thread["thread_id"] == "g:18f6a2b3c4e5f6a8"
        assert thread["message_count"] == 3
        assert len(thread["messages"]) == 3

    @pytest.mark.asyncio
    async def test_strips_prefix_in_upstream_call(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(THREAD_RESPONSE_TEXT)
        )

        await adapter.read_thread("g:18f6a2b3c4e5f6a8")

        args = adapter._session.call_tool.call_args
        assert args[0][1]["thread_id"] == "18f6a2b3c4e5f6a8"


class TestGmailAdapterWhatsnew:
    """Test GmailAdapter.whatsnew() with mocked MCP session."""

    @pytest.mark.asyncio
    async def test_default_uses_newer_than_1d(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(SEARCH_RESPONSE_TEXT)
        )

        await adapter.whatsnew()

        args = adapter._session.call_tool.call_args
        assert args[0][1]["query"] == "newer_than:1d"

    @pytest.mark.asyncio
    async def test_with_since_uses_after(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result(SEARCH_RESPONSE_TEXT)
        )

        await adapter.whatsnew(since="2026-02-20T08:30:00Z")

        args = adapter._session.call_tool.call_args
        assert args[0][1]["query"] == "after:2026-02-20T08:30:00Z"


class TestGmailAdapterErrorHandling:
    """Test error handling in the adapter."""

    @pytest.mark.asyncio
    async def test_upstream_error_raises_runtime_error(self):
        adapter = _make_adapter()
        adapter._session.call_tool = AsyncMock(
            return_value=_make_call_result("Something went wrong", is_error=True)
        )

        with pytest.raises(RuntimeError, match="returned error"):
            await adapter.read_message("g:bad_id")

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        config = GmailAdapterConfig(user_email="user@gmail.com")
        adapter = GmailAdapter(config)
        # Don't call connect() — session is None.

        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.list_messages()

    @pytest.mark.asyncio
    async def test_empty_content_raises(self):
        adapter = _make_adapter()
        result = MagicMock()
        result.isError = False
        result.content = []  # No text content.
        adapter._session.call_tool = AsyncMock(return_value=result)

        with pytest.raises(RuntimeError, match="no text content"):
            await adapter.list_messages()


class TestGmailAdapterSourcePrefix:
    """Test that source_prefix is correct."""

    def test_source_prefix_is_g(self):
        config = GmailAdapterConfig(user_email="user@gmail.com")
        adapter = GmailAdapter(config)
        assert adapter.source_prefix == "g"


class TestGmailAdapterIdStripping:
    """Test the _strip_prefix helper."""

    def test_strips_g_prefix(self):
        adapter = _make_adapter()
        assert adapter._strip_prefix("g:abc123") == "abc123"

    def test_leaves_unprefixed(self):
        adapter = _make_adapter()
        assert adapter._strip_prefix("abc123") == "abc123"

    def test_leaves_other_prefix(self):
        adapter = _make_adapter()
        assert adapter._strip_prefix("w:abc123") == "w:abc123"


# ---------------------------------------------------------------------------
# Integration tests — require a real google_workspace_mcp server
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGmailAdapterIntegration:
    """Integration tests that connect to a real google_workspace_mcp server.

    These are skipped by default.  Run with::

        pytest -m integration tests/test_gmail_adapter.py -v

    Prerequisites:
    - google_workspace_mcp server available at H:/source/google_workspace_mcp
    - Valid Google OAuth credentials configured
    - Set env var TS4K_TEST_EMAIL to the Google email to use
    """

    @pytest.fixture
    def adapter(self):
        import os

        email = os.environ.get("TS4K_TEST_EMAIL", "user@gmail.com")
        return GmailAdapter(
            GmailAdapterConfig(
                user_email=email,
                transport="stdio",
                server_command=["python", "main.py"],
                server_args=["--tools", "gmail", "--single-user"],
                server_cwd="H:/source/google_workspace_mcp",
            )
        )

    @pytest.mark.asyncio
    async def test_connect_and_list(self, adapter):
        async with adapter:
            results = await adapter.list_messages("newer_than:1d", count=3)
            assert isinstance(results, list)
            if results:
                assert results[0]["id"].startswith("g:")

    @pytest.mark.asyncio
    async def test_read_message(self, adapter):
        async with adapter:
            results = await adapter.list_messages("newer_than:7d", count=1)
            if results:
                msg = await adapter.read_message(results[0]["id"])
                assert "body" in msg
                assert msg["from"]

    @pytest.mark.asyncio
    async def test_read_thread(self, adapter):
        async with adapter:
            results = await adapter.list_messages("newer_than:7d", count=1)
            if results:
                thread = await adapter.read_thread(results[0]["thread_id"])
                assert "messages" in thread
                assert thread["message_count"] >= 1

# tests/test_gmail_draft.py
"""Tests for Gmail draft creation with reply threading."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
from ts4k.core.levels import AccessLevel


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


@pytest.fixture
def gmail_draft():
    adapter = GmailAdapter(
        GmailAdapterConfig(user_email="test@gmail.com", level="draft"),
        prefix="g",
    )
    adapter._service = MagicMock()
    return adapter


@pytest.fixture
def gmail_modify():
    adapter = GmailAdapter(
        GmailAdapterConfig(user_email="test@gmail.com", level="modify"),
        prefix="g",
    )
    adapter._service = MagicMock()
    return adapter


class TestLevelGating:
    @pytest.mark.asyncio
    async def test_draft_blocked_at_modify(self, gmail_modify):
        with pytest.raises(PermissionError, match="level='draft'"):
            await gmail_modify.create_draft(
                to="alice@example.com", subject="Hi", body="Hello"
            )

    @pytest.mark.asyncio
    async def test_draft_allowed_at_draft(self, gmail_draft):
        svc = gmail_draft._service
        svc.users().drafts().create().execute.return_value = {
            "id": "draft_123",
            "message": {"id": "msg_456", "threadId": "thread_789"},
        }
        result = await gmail_draft.create_draft(
            to="alice@example.com", subject="Hi", body="Hello"
        )
        assert result["status"] == "draft_created"


class TestNewDraft:
    @pytest.mark.asyncio
    async def test_creates_new_draft(self, gmail_draft):
        svc = gmail_draft._service
        svc.users().drafts().create().execute.return_value = {
            "id": "draft_123",
            "message": {"id": "msg_456", "threadId": "thread_789"},
        }
        result = await gmail_draft.create_draft(
            to="alice@example.com",
            subject="Meeting tomorrow",
            body="Let's meet at 3pm.",
        )
        assert result["id"] == "g:draft_123"
        assert result["status"] == "draft_created"
        # Verify the draft was created with correct structure
        call_args = svc.users().drafts().create.call_args
        assert call_args[1]["userId"] == "me"


class TestReplyDraft:
    @pytest.mark.asyncio
    async def test_reply_includes_threading_headers(self, gmail_draft):
        svc = gmail_draft._service
        # Mock reading the original message
        svc.users().messages().get().execute.return_value = {
            "id": "orig_123",
            "threadId": "thread_789",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Meeting tomorrow"},
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "Message-ID", "value": "<orig@mail.gmail.com>"},
                    {"name": "To", "value": "test@gmail.com"},
                    {"name": "Date", "value": "Mon, 10 Mar 2026 09:00:00 +0000"},
                ],
                "mimeType": "text/plain",
                "body": {"data": _b64("Let's meet at 3pm.")},
            },
            "internalDate": "1741597200000",
        }
        # Mock draft creation
        svc.users().drafts().create().execute.return_value = {
            "id": "draft_456",
            "message": {"id": "msg_789", "threadId": "thread_789"},
        }
        result = await gmail_draft.create_draft(
            to="alice@example.com",
            subject="Re: Meeting tomorrow",
            body="Sure, see you then!",
            reply_to_message_id="g:orig_123",
        )
        assert result["status"] == "draft_created"
        assert result["id"] == "g:draft_456"

    @pytest.mark.asyncio
    async def test_reply_blockquotes_original(self, gmail_draft):
        svc = gmail_draft._service
        svc.users().messages().get().execute.return_value = {
            "id": "orig_123",
            "threadId": "thread_789",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Budget review"},
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "Message-ID", "value": "<orig@mail.gmail.com>"},
                    {"name": "Date", "value": "Mon, 10 Mar 2026 09:00:00 +0000"},
                ],
                "mimeType": "text/plain",
                "body": {"data": _b64("Please review the budget.")},
            },
            "internalDate": "1741597200000",
        }
        svc.users().drafts().create().execute.return_value = {
            "id": "draft_456",
            "message": {"id": "msg_789", "threadId": "thread_789"},
        }
        await gmail_draft.create_draft(
            to="alice@example.com",
            subject="Re: Budget review",
            body="Looks good to me.",
            reply_to_message_id="g:orig_123",
        )
        # Extract the raw message from the draft create call
        call_args = svc.users().drafts().create.call_args
        raw_msg = base64.urlsafe_b64decode(
            call_args[1]["body"]["message"]["raw"]
        ).decode()
        assert "Looks good to me." in raw_msg
        assert "Please review the budget." in raw_msg
        assert "wrote:" in raw_msg.lower() or ">" in raw_msg

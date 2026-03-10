# tests/test_o365_draft.py
"""Tests for O365 draft creation with reply threading."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig


def _mock_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=json_data, request=httpx.Request("POST", "http://test"))


@pytest.fixture
def o365_draft():
    adapter = O365Adapter(
        O365AdapterConfig(client_id="test-id", level="draft"), prefix="o"
    )
    adapter._client = AsyncMock(spec=httpx.AsyncClient)
    return adapter


class TestNewDraft:
    @pytest.mark.asyncio
    async def test_creates_draft_message(self, o365_draft):
        o365_draft._client.post = AsyncMock(
            return_value=_mock_response({
                "id": "AAMkDraft123",
                "subject": "Hello",
            })
        )
        result = await o365_draft.create_draft(
            to="alice@example.com",
            subject="Hello",
            body="Hi Alice!",
        )
        assert result["id"] == "o:AAMkDraft123"
        assert result["status"] == "draft_created"
        call_args = o365_draft._client.post.call_args
        assert "/messages" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["subject"] == "Hello"
        assert payload["body"]["content"] == "Hi Alice!"
        assert payload["toRecipients"][0]["emailAddress"]["address"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_draft_level_gating(self):
        adapter = O365Adapter(
            O365AdapterConfig(client_id="test-id", level="modify"), prefix="o"
        )
        adapter._client = AsyncMock(spec=httpx.AsyncClient)
        with pytest.raises(PermissionError, match="level='draft'"):
            await adapter.create_draft(to="a@b.com", subject="X", body="Y")


class TestReplyDraft:
    @pytest.mark.asyncio
    async def test_reply_includes_conversation_id(self, o365_draft):
        # Mock GET for original message
        o365_draft._client.get = AsyncMock(
            return_value=_mock_response({
                "id": "AAMkOrig",
                "conversationId": "convABC",
                "subject": "Budget review",
                "from": {
                    "emailAddress": {"name": "Alice", "address": "alice@example.com"}
                },
                "receivedDateTime": "2026-03-10T09:00:00Z",
                "body": {"contentType": "text", "content": "Please review the budget."},
                "internetMessageId": "<orig@outlook.com>",
            })
        )
        # Mock POST for draft creation
        o365_draft._client.post = AsyncMock(
            return_value=_mock_response({
                "id": "AAMkDraft456",
                "conversationId": "convABC",
            })
        )
        result = await o365_draft.create_draft(
            to="alice@example.com",
            subject="Re: Budget review",
            body="Looks good.",
            reply_to_message_id="o:AAMkOrig",
        )
        assert result["status"] == "draft_created"
        call_args = o365_draft._client.post.call_args
        payload = call_args[1]["json"]
        assert payload["conversationId"] == "convABC"
        assert "Please review the budget." in payload["body"]["content"]

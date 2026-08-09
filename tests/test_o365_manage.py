# tests/test_o365_manage.py
"""Tests for O365 mailbox management methods."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig


def _mock_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=json_data, request=httpx.Request("POST", "http://test"))


@pytest.fixture
def o365_modify():
    adapter = O365Adapter(
        O365AdapterConfig(client_id="test-id", level="modify"), prefix="o"
    )
    adapter._client = AsyncMock(spec=httpx.AsyncClient)
    return adapter


@pytest.fixture
def o365_readonly():
    adapter = O365Adapter(
        O365AdapterConfig(client_id="test-id"), prefix="o"
    )
    adapter._client = AsyncMock(spec=httpx.AsyncClient)
    return adapter


class TestLevelGating:
    @pytest.mark.asyncio
    async def test_archive_blocked_at_readonly(self, o365_readonly):
        with pytest.raises(PermissionError, match="level='modify'"):
            await o365_readonly.archive_message("o:AAMk123")


class TestArchive:
    @pytest.mark.asyncio
    async def test_archive_moves_to_archive_folder(self, o365_modify):
        o365_modify._client.post = AsyncMock(
            return_value=_mock_response({"id": "AAMk123"})
        )
        result = await o365_modify.archive_message("o:AAMk123")
        o365_modify._client.post.assert_called_once()
        call_args = o365_modify._client.post.call_args
        assert "/messages/AAMk123/move" in call_args[0][0]
        assert result["status"] == "archived"

    @pytest.mark.asyncio
    async def test_unarchive_moves_to_inbox(self, o365_modify):
        o365_modify._client.post = AsyncMock(
            return_value=_mock_response({"id": "AAMk123"})
        )
        result = await o365_modify.unarchive_message("o:AAMk123")
        assert result["status"] == "unarchived"


class TestMarkRead:
    @pytest.mark.asyncio
    async def test_mark_read(self, o365_modify):
        o365_modify._client.patch = AsyncMock(
            return_value=_mock_response({"id": "AAMk123", "isRead": True})
        )
        result = await o365_modify.mark_read("o:AAMk123")
        call_args = o365_modify._client.patch.call_args
        assert call_args[1]["json"] == {"isRead": True}
        assert result["status"] == "marked_read"

    @pytest.mark.asyncio
    async def test_mark_unread(self, o365_modify):
        o365_modify._client.patch = AsyncMock(
            return_value=_mock_response({"id": "AAMk123", "isRead": False})
        )
        result = await o365_modify.mark_unread("o:AAMk123")
        call_args = o365_modify._client.patch.call_args
        assert call_args[1]["json"] == {"isRead": False}
        assert result["status"] == "marked_unread"


class TestCategorize:
    @pytest.mark.asyncio
    async def test_categorize(self, o365_modify):
        o365_modify._client.get = AsyncMock(
            return_value=_mock_response({"id": "AAMk123", "categories": []})
        )
        o365_modify._client.patch = AsyncMock(
            return_value=_mock_response({"id": "AAMk123", "categories": ["llm-garbage"]})
        )
        result = await o365_modify.label_message("o:AAMk123", "llm-garbage")
        call_args = o365_modify._client.patch.call_args
        assert "llm-garbage" in call_args[1]["json"]["categories"]
        assert result["status"] == "labeled"


class TestMoveToFolder:
    @pytest.mark.asyncio
    async def test_move_to_folder(self, o365_modify):
        # First call: list folders (find or create)
        o365_modify._client.get = AsyncMock(
            return_value=_mock_response({
                "value": [{"id": "folder123", "displayName": "llm-garbage"}]
            })
        )
        o365_modify._client.post = AsyncMock(
            return_value=_mock_response({"id": "AAMk123"})
        )
        result = await o365_modify.move_to_folder("o:AAMk123", "llm-garbage")
        assert result["status"] == "moved"

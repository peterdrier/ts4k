"""Unit tests for WhatsAppAdapter.download_media (ts4k#49).

The bridge's ``download_media`` tool needs both a message ID and its chat
JID; the adapter resolves the JID by reading the message first. These pin
that resolution and the response parsing against a mocked ``_call_tool`` —
no live bridge involved.
"""

from __future__ import annotations

import json

import pytest

from ts4k.adapters.whatsapp import WhatsAppAdapter, WhatsAppAdapterConfig


def _adapter() -> WhatsAppAdapter:
    return WhatsAppAdapter(WhatsAppAdapterConfig())


def _context_response(chat_jid: str | None) -> str:
    message = {"id": "msg1", "content": "[image]", "timestamp": "2026-08-10 09:00:00"}
    if chat_jid is not None:
        message["chat_jid"] = chat_jid
    return json.dumps({"message": message, "before": [], "after": []})


@pytest.mark.asyncio
async def test_resolves_chat_jid_then_calls_bridge(monkeypatch):
    adapter = _adapter()
    calls = []

    async def fake_call_tool(name, arguments):
        calls.append((name, arguments))
        if name == "get_message_context":
            return _context_response("123@s.whatsapp.net")
        if name == "download_media":
            return json.dumps({
                "success": True,
                "message": "Media downloaded successfully",
                "file_path": "/store/media/123/msg1.jpg",
                "is_thumbnail": False,
            })
        raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(adapter, "_call_tool", fake_call_tool)

    result = await adapter.download_media("w:msg1")

    assert calls[0] == ("get_message_context", {"message_id": "msg1", "before": 0, "after": 0})
    assert calls[1] == ("download_media", {"message_id": "msg1", "chat_jid": "123@s.whatsapp.net"})
    assert result == {
        "success": True,
        "file_path": "/store/media/123/msg1.jpg",
        "message": "Media downloaded successfully",
    }


@pytest.mark.asyncio
async def test_bridge_failure_is_reported_not_raised(monkeypatch):
    adapter = _adapter()

    async def fake_call_tool(name, arguments):
        if name == "get_message_context":
            return _context_response("123@s.whatsapp.net")
        return json.dumps({"success": False, "message": "Failed to download media"})

    monkeypatch.setattr(adapter, "_call_tool", fake_call_tool)

    result = await adapter.download_media("w:msg1")

    assert result["success"] is False
    assert result["file_path"] is None
    assert result["message"] == "Failed to download media"


@pytest.mark.asyncio
async def test_missing_chat_jid_never_calls_download_tool(monkeypatch):
    adapter = _adapter()
    calls = []

    async def fake_call_tool(name, arguments):
        calls.append(name)
        return _context_response(None)

    monkeypatch.setattr(adapter, "_call_tool", fake_call_tool)

    result = await adapter.download_media("w:msg1")

    assert calls == ["get_message_context"]  # download_media never called
    assert result["success"] is False
    assert result["file_path"] is None
    assert "chat" in result["message"].lower()

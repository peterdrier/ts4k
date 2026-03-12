"""Tests for O365CalAdapter write operations."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

from ts4k.adapters.o365cal import O365CalAdapter, O365CalAdapterConfig
from ts4k.core.levels import AccessLevel


def _mock_response(data: dict | None = None, status_code: int = 200) -> httpx.Response:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if data is not None:
        resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _make_adapter(level: str = "send", prefix: str = "oc") -> O365CalAdapter:
    config = O365CalAdapterConfig(
        email="user@contoso.com",
        client_id="test-client-id",
        tenant_id="test-tenant-id",
        calendar_id="default",
        calendar_name="Test",
        timezone="Europe/Amsterdam",
        level=level,
    )
    adapter = O365CalAdapter(config, prefix=prefix)
    adapter._client = AsyncMock(spec=httpx.AsyncClient)
    return adapter


class TestCreateEventLevelGating:
    async def test_readonly_rejected(self):
        adapter = _make_adapter(level="readonly")
        with pytest.raises(PermissionError, match="draft"):
            await adapter.create_event(title="Test", start="2026-03-11T09:00:00", end="2026-03-11T10:00:00")

    async def test_draft_allowed_no_attendees(self):
        adapter = _make_adapter(level="draft")
        created = {
            "id": "new1", "subject": "Test",
            "start": {"dateTime": "2026-03-11T09:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-11T10:00:00", "timeZone": "UTC"},
            "isAllDay": False, "isCancelled": False,
            "location": {"displayName": ""},
            "responseStatus": {"response": "organizer"},
        }
        adapter._client.post = AsyncMock(return_value=_mock_response(created))
        result = await adapter.create_event(title="Test", start="2026-03-11T09:00:00", end="2026-03-11T10:00:00")
        assert result["id"] == "oc:new1"

    async def test_draft_rejected_with_attendees(self):
        adapter = _make_adapter(level="draft")
        with pytest.raises(PermissionError, match="send"):
            await adapter.create_event(
                title="Test", start="2026-03-11T09:00:00", end="2026-03-11T10:00:00",
                attendees=["alice@example.com"],
            )

    async def test_send_allowed_with_attendees(self):
        adapter = _make_adapter(level="send")
        created = {
            "id": "new2", "subject": "Team Sync",
            "start": {"dateTime": "2026-03-11T09:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-11T10:00:00", "timeZone": "UTC"},
            "isAllDay": False, "isCancelled": False,
            "attendees": [{"emailAddress": {"address": "alice@example.com"}, "status": {"response": "none"}}],
            "location": {"displayName": ""},
            "responseStatus": {"response": "organizer"},
        }
        adapter._client.post = AsyncMock(return_value=_mock_response(created))
        result = await adapter.create_event(
            title="Team Sync", start="2026-03-11T09:00:00", end="2026-03-11T10:00:00",
            attendees=["alice@example.com"],
        )
        assert result["id"] == "oc:new2"


class TestCreateEventAllDay:
    async def test_inclusive_end_date_converted(self):
        adapter = _make_adapter(level="draft")
        created = {
            "id": "new3", "subject": "Vacation",
            "start": {"dateTime": "2026-03-17", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-22", "timeZone": "UTC"},
            "isAllDay": True, "isCancelled": False,
            "location": {"displayName": ""},
            "responseStatus": {"response": "organizer"},
        }
        adapter._client.post = AsyncMock(return_value=_mock_response(created))
        await adapter.create_event(title="Vacation", start="2026-03-17", end="2026-03-21")

        call_args = adapter._client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json", {})
        assert body["start"]["dateTime"] == "2026-03-17"
        assert body["end"]["dateTime"] == "2026-03-22"  # +1 day
        assert body["isAllDay"] is True


class TestUpdateEvent:
    async def test_modify_level_required(self):
        adapter = _make_adapter(level="readonly")
        with pytest.raises(PermissionError):
            await adapter.update_event("oc:evt1", title="New Title")

    async def test_patches_fields(self):
        adapter = _make_adapter(level="modify")
        updated = {
            "id": "evt1", "subject": "New Title",
            "start": {"dateTime": "2026-03-11T09:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-11T10:00:00", "timeZone": "UTC"},
            "isAllDay": False, "isCancelled": False,
            "location": {"displayName": ""},
            "responseStatus": {"response": "organizer"},
        }
        adapter._client.patch = AsyncMock(return_value=_mock_response(updated))
        result = await adapter.update_event("oc:evt1", title="New Title")
        assert result["title"] == "New Title"


class TestRsvp:
    async def test_modify_level_required(self):
        adapter = _make_adapter(level="readonly")
        with pytest.raises(PermissionError):
            await adapter.rsvp("oc:evt1", status="accepted")

    async def test_rsvp_accepted(self):
        adapter = _make_adapter(level="modify")
        adapter._client.post = AsyncMock(return_value=_mock_response(status_code=202))
        refetched = {
            "id": "evt1", "subject": "Meeting",
            "start": {"dateTime": "2026-03-11T09:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-11T10:00:00", "timeZone": "UTC"},
            "isAllDay": False, "isCancelled": False,
            "location": {"displayName": ""},
            "responseStatus": {"response": "accepted"},
            "attendees": [
                {"emailAddress": {"name": "User", "address": "user@contoso.com"}, "status": {"response": "accepted"}},
            ],
            "body": {"content": ""},
            "createdDateTime": "", "lastModifiedDateTime": "",
        }
        adapter._client.get = AsyncMock(return_value=_mock_response(refetched))
        result = await adapter.rsvp("oc:evt1", status="accepted")
        assert result["your_status"] == "accepted"

    async def test_rsvp_declined(self):
        adapter = _make_adapter(level="modify")
        adapter._client.post = AsyncMock(return_value=_mock_response(status_code=202))
        refetched = {
            "id": "evt1", "subject": "Meeting",
            "start": {"dateTime": "2026-03-11T09:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-11T10:00:00", "timeZone": "UTC"},
            "isAllDay": False, "isCancelled": False,
            "location": {"displayName": ""},
            "responseStatus": {"response": "declined"},
            "attendees": [],
            "body": {"content": ""},
            "createdDateTime": "", "lastModifiedDateTime": "",
        }
        adapter._client.get = AsyncMock(return_value=_mock_response(refetched))
        result = await adapter.rsvp("oc:evt1", status="declined")
        assert result["your_status"] == "declined"

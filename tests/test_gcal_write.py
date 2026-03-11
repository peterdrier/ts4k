"""Tests for GcalAdapter write operations."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from ts4k.adapters.gcal import GcalAdapter, GcalAdapterConfig
from ts4k.core.levels import AccessLevel


def _make_adapter(level: str = "send", prefix: str = "gc") -> GcalAdapter:
    config = GcalAdapterConfig(
        email="test@gmail.com",
        calendar_id="primary",
        calendar_name="Test",
        timezone="Europe/Amsterdam",
        level=level,
    )
    adapter = GcalAdapter(config, prefix=prefix)
    adapter._service = MagicMock()
    return adapter


class TestCreateEventLevelGating:
    async def test_readonly_rejected(self):
        adapter = _make_adapter(level="readonly")
        with pytest.raises(PermissionError, match="draft"):
            await adapter.create_event(title="Test", start="2026-03-11T09:00:00Z", end="2026-03-11T10:00:00Z")

    async def test_draft_allowed_no_attendees(self):
        adapter = _make_adapter(level="draft")
        adapter._service.events.return_value.insert.return_value.execute.return_value = {
            "id": "new1", "summary": "Test",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
        }
        result = await adapter.create_event(title="Test", start="2026-03-11T09:00:00Z", end="2026-03-11T10:00:00Z")
        assert result["id"] == "gc:new1"

    async def test_draft_rejected_with_attendees(self):
        adapter = _make_adapter(level="draft")
        with pytest.raises(PermissionError, match="send"):
            await adapter.create_event(
                title="Test", start="2026-03-11T09:00:00Z", end="2026-03-11T10:00:00Z",
                attendees=["alice@example.com"],
            )

    async def test_send_allowed_with_attendees(self):
        adapter = _make_adapter(level="send")
        adapter._service.events.return_value.insert.return_value.execute.return_value = {
            "id": "new2", "summary": "Team Sync",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
            "attendees": [{"email": "alice@example.com"}],
        }
        result = await adapter.create_event(
            title="Team Sync", start="2026-03-11T09:00:00Z", end="2026-03-11T10:00:00Z",
            attendees=["alice@example.com"],
        )
        assert result["id"] == "gc:new2"


class TestCreateEventAllDay:
    async def test_inclusive_end_date_converted(self):
        """CLI provides inclusive end date; adapter adds +1 day for API."""
        adapter = _make_adapter(level="draft")
        adapter._service.events.return_value.insert.return_value.execute.return_value = {
            "id": "new3", "summary": "Vacation",
            "start": {"date": "2026-03-17"},
            "end": {"date": "2026-03-22"},
            "status": "confirmed",
        }
        await adapter.create_event(title="Vacation", start="2026-03-17", end="2026-03-21")

        call_args = adapter._service.events.return_value.insert.call_args
        body = call_args[1]["body"]
        assert body["start"] == {"date": "2026-03-17"}
        assert body["end"] == {"date": "2026-03-22"}  # +1 day


class TestUpdateEvent:
    async def test_modify_level_required(self):
        adapter = _make_adapter(level="readonly")
        with pytest.raises(PermissionError):
            await adapter.update_event("gc:evt1", title="New Title")

    async def test_patches_fields(self):
        adapter = _make_adapter(level="modify")
        adapter._service.events.return_value.patch.return_value.execute.return_value = {
            "id": "evt1", "summary": "New Title",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
        }
        result = await adapter.update_event("gc:evt1", title="New Title")
        assert result["title"] == "New Title"


class TestRsvp:
    async def test_modify_level_required(self):
        adapter = _make_adapter(level="readonly")
        with pytest.raises(PermissionError):
            await adapter.rsvp("gc:evt1", status="accepted")

    async def test_updates_self_status(self):
        adapter = _make_adapter(level="modify")
        adapter._service.events.return_value.get.return_value.execute.return_value = {
            "id": "evt1", "summary": "Meeting",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
            "attendees": [
                {"email": "test@gmail.com", "self": True, "responseStatus": "needsAction"},
                {"email": "other@work.com", "responseStatus": "accepted"},
            ],
        }
        adapter._service.events.return_value.patch.return_value.execute.return_value = {
            "id": "evt1", "summary": "Meeting",
            "start": {"dateTime": "2026-03-11T09:00:00Z"},
            "end": {"dateTime": "2026-03-11T10:00:00Z"},
            "status": "confirmed",
            "attendees": [
                {"email": "test@gmail.com", "self": True, "responseStatus": "accepted"},
                {"email": "other@work.com", "responseStatus": "accepted"},
            ],
        }
        result = await adapter.rsvp("gc:evt1", status="accepted")
        assert result["your_status"] == "accepted"

# tests/test_gmail_manage.py
"""Tests for Gmail mailbox management methods."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig


@pytest.fixture
def gmail_modify():
    """GmailAdapter at modify level with mocked service."""
    adapter = GmailAdapter(
        GmailAdapterConfig(user_email="test@gmail.com", level="modify"),
        prefix="g",
    )
    adapter._service = MagicMock()
    return adapter


@pytest.fixture
def gmail_readonly():
    """GmailAdapter at readonly level with mocked service."""
    adapter = GmailAdapter(
        GmailAdapterConfig(user_email="test@gmail.com"),
        prefix="g",
    )
    adapter._service = MagicMock()
    return adapter


class TestLevelGating:
    @pytest.mark.asyncio
    async def test_archive_blocked_at_readonly(self, gmail_readonly):
        with pytest.raises(PermissionError, match="level='modify'"):
            await gmail_readonly.archive_message("g:abc123")

    @pytest.mark.asyncio
    async def test_archive_allowed_at_modify(self, gmail_modify):
        gmail_modify._service.users().messages().modify().execute.return_value = {
            "id": "abc123", "labelIds": ["CATEGORY_PERSONAL"]
        }
        result = await gmail_modify.archive_message("g:abc123")
        assert result["status"] == "archived"


class TestArchive:
    @pytest.mark.asyncio
    async def test_archive_removes_inbox_label(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().messages().modify().execute.return_value = {
            "id": "abc123", "labelIds": ["CATEGORY_PERSONAL"]
        }
        result = await gmail_modify.archive_message("g:abc123")
        svc.users().messages().modify.assert_called_with(
            userId="me", id="abc123",
            body={"removeLabelIds": ["INBOX"]}
        )
        assert result["status"] == "archived"
        assert result["id"] == "g:abc123"

    @pytest.mark.asyncio
    async def test_unarchive_adds_inbox_label(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().messages().modify().execute.return_value = {
            "id": "abc123", "labelIds": ["INBOX"]
        }
        result = await gmail_modify.unarchive_message("g:abc123")
        svc.users().messages().modify.assert_called_with(
            userId="me", id="abc123",
            body={"addLabelIds": ["INBOX"]}
        )
        assert result["status"] == "unarchived"


class TestLabel:
    @pytest.mark.asyncio
    async def test_label_creates_if_missing(self, gmail_modify):
        svc = gmail_modify._service
        # labels.list returns no match
        svc.users().labels().list().execute.return_value = {
            "labels": [{"id": "INBOX", "name": "INBOX"}]
        }
        # labels.create returns new label
        svc.users().labels().create().execute.return_value = {
            "id": "Label_99", "name": "llm-garbage"
        }
        # modify succeeds
        svc.users().messages().modify().execute.return_value = {
            "id": "abc123", "labelIds": ["Label_99"]
        }
        result = await gmail_modify.label_message("g:abc123", "llm-garbage")
        assert result["status"] == "labeled"
        assert result["label"] == "llm-garbage"

    @pytest.mark.asyncio
    async def test_unlabel(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().labels().list().execute.return_value = {
            "labels": [{"id": "Label_99", "name": "llm-garbage"}]
        }
        svc.users().messages().modify().execute.return_value = {
            "id": "abc123", "labelIds": []
        }
        result = await gmail_modify.unlabel_message("g:abc123", "llm-garbage")
        assert result["status"] == "unlabeled"


class TestMarkRead:
    @pytest.mark.asyncio
    async def test_mark_read(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().messages().modify().execute.return_value = {
            "id": "abc123", "labelIds": []
        }
        result = await gmail_modify.mark_read("g:abc123")
        svc.users().messages().modify.assert_called_with(
            userId="me", id="abc123",
            body={"removeLabelIds": ["UNREAD"]}
        )
        assert result["status"] == "marked_read"

    @pytest.mark.asyncio
    async def test_mark_unread(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().messages().modify().execute.return_value = {
            "id": "abc123", "labelIds": ["UNREAD"]
        }
        result = await gmail_modify.mark_unread("g:abc123")
        svc.users().messages().modify.assert_called_with(
            userId="me", id="abc123",
            body={"addLabelIds": ["UNREAD"]}
        )
        assert result["status"] == "marked_unread"


class TestTrash:
    @pytest.mark.asyncio
    async def test_trash(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().messages().trash().execute.return_value = {
            "id": "abc123", "labelIds": ["TRASH"]
        }
        result = await gmail_modify.trash_message("g:abc123")
        assert result["status"] == "trashed"


class TestListLabels:
    @pytest.mark.asyncio
    async def test_list_labels(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().labels().list().execute.return_value = {
            "labels": [
                {"id": "INBOX", "name": "INBOX", "type": "system"},
                {"id": "Label_1", "name": "invoices", "type": "user"},
            ]
        }
        result = await gmail_modify.list_labels()
        assert len(result) == 2
        assert result[1]["name"] == "invoices"

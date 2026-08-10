# tests/test_manage_commands.py
"""Tests for manage and draft command functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ts4k.commands import manage_message, create_draft


class TestManageMessage:
    @pytest.mark.asyncio
    async def test_archive(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k.state import sources
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
        sources.add("g", provider="gmail", email="a@b.com", level="modify")

        mock_adapter = AsyncMock()
        mock_adapter.archive_message.return_value = {"id": "g:abc", "status": "archived"}
        mock_adapter.source_prefix = "g"

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter):
            result = await manage_message(action="archive", msg_id="g:abc")
        assert "archived" in result

    @pytest.mark.asyncio
    async def test_dry_run(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k.state import sources
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
        sources.add("g", provider="gmail", email="a@b.com", level="modify")

        result = await manage_message(action="archive", msg_id="g:abc", dry_run=True)
        assert "dry-run" in result.lower() or "would" in result.lower()

    @pytest.mark.asyncio
    async def test_batch_ids(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k.state import sources
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
        sources.add("g", provider="gmail", email="a@b.com", level="modify")

        mock_adapter = AsyncMock()
        mock_adapter.archive_message.return_value = {"id": "g:x", "status": "archived"}
        mock_adapter.source_prefix = "g"

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter):
            await manage_message(
                action="archive", msg_id="g:abc,g:def,g:ghi"
            )
        assert mock_adapter.archive_message.call_count == 3


class TestCreateDraft:
    @pytest.mark.asyncio
    async def test_create_draft(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k.state import sources
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
        sources.add("g", provider="gmail", email="a@b.com", level="draft")

        mock_adapter = AsyncMock()
        mock_adapter.create_draft.return_value = {"id": "g:draft_1", "status": "draft_created"}
        mock_adapter.source_prefix = "g"

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter):
            result = await create_draft(
                source="g", to="alice@x.com", subject="Hi", body="Hello"
            )
        assert "draft_created" in result

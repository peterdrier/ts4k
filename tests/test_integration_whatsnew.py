"""Integration test: whatsnew + updates end-to-end with mock adapters."""

from __future__ import annotations

import json

import pytest

from ts4k import commands
from ts4k.commands import CommandResult
from ts4k.state import keyed_watermarks as kwm


@pytest.fixture(autouse=True)
def mock_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))

    from ts4k.state import sources, stats, cache

    monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(stats, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(stats, "_STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr(cache, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(kwm, "_CONFIG_DIR", tmp_path)

    cfg = {
        "g": {"provider": "gmail", "email": "t@t.com"},
        "o": {"provider": "o365", "client_id": "fake", "tenant_id": "common"},
    }
    (tmp_path / "sources.json").write_text(json.dumps(cfg))
    return tmp_path


def _fake_messages(prefix, count, base_hour=10):
    return [
        {
            "id": f"{prefix}:msg{i}",
            "source": prefix,
            "thread_id": f"{prefix}:t{i}",
            "from": f"s{i}@test.com",
            "subject": f"Subj {i}",
            "date": f"2026-03-08T{base_hour + i:02d}:00:00Z",
            "body": f"Body {i}",
        }
        for i in range(count)
    ]


class TestWhatsnewDrains:
    @pytest.mark.asyncio
    async def test_whatsnew_advances_watermarks(self, monkeypatch):
        """whatsnew saves per-source watermarks from returned messages."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 5, base_hour=10)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        r1 = await commands.whatsnew(key="test", count=20)
        assert r1.messages_processed > 0
        wm = kwm.get("test", "g")
        assert wm is not None
        assert "T" in wm

    @pytest.mark.asyncio
    async def test_whatsnew_second_call_uses_saved_watermarks(self, monkeypatch):
        """Second whatsnew call passes saved watermarks to _fetch_messages."""
        call_since = []

        async def fake_fetch_messages(**kwargs):
            call_since.append(dict(kwargs.get("since", {})))
            return CommandResult(
                output="ok",
                messages_processed=1,
                _messages=[{"source": "g", "date": "2026-03-08T15:00:00Z"}],
            )

        monkeypatch.setattr(commands, "_fetch_messages", fake_fetch_messages)

        await commands.whatsnew(key="drain_test")
        await commands.whatsnew(key="drain_test")

        assert len(call_since) == 2
        # Second call should use the watermark from first call
        assert call_since[1]["g"] == "2026-03-08T15:00:00Z"


class TestUpdatesStateless:
    @pytest.mark.asyncio
    async def test_updates_does_not_create_watermarks(self, monkeypatch, mock_env):
        """updates never creates watermark files."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 3)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        await commands.updates(since="1d", count=20)
        assert kwm.list_keys() == []

    @pytest.mark.asyncio
    async def test_updates_returns_messages(self, monkeypatch):
        """updates returns formatted output."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 3)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands.updates(since="1d", count=20)
        assert result.messages_processed > 0
        assert result.output


class TestHasMoreTruncation:
    @pytest.mark.asyncio
    async def test_truncation_sets_has_more(self, monkeypatch):
        """When total fetched > count, has_more=True with remaining count."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 15, base_hour=1)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands.updates(since="1d", count=5)
        assert result.has_more is True
        assert result.remaining > 0
        assert "more messages available" in result.output

    @pytest.mark.asyncio
    async def test_no_truncation_when_within_count(self, monkeypatch):
        """When total <= count, has_more is False."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 3)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands.updates(since="1d", count=20)
        assert result.has_more is False
        assert result.remaining == 0


class TestIndependentKeys:
    @pytest.mark.asyncio
    async def test_keys_dont_interfere(self, monkeypatch):
        """Different whatsnew keys maintain independent watermarks."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 2, base_hour=10)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        await commands.whatsnew(key="life")
        await commands.whatsnew(key="peter")

        life_wm = kwm.get_all("life")
        peter_wm = kwm.get_all("peter")
        assert life_wm
        assert peter_wm


class TestMultiSourceFetch:
    @pytest.mark.asyncio
    async def test_messages_sorted_newest_first(self, monkeypatch):
        """Messages from multiple sources are merged and sorted by date descending."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            if prefix == "g":
                return _fake_messages("g", 2, base_hour=14)
            return _fake_messages("o", 2, base_hour=10)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands.updates(since="1d", count=20)
        lines = result.output.strip().split("\n")
        # Find first data line (skip header and date grouping lines)
        data_lines = [l for l in lines[1:] if l and not l.startswith("---") and not l.startswith("N|")]
        if data_lines:
            assert "g" in data_lines[0]  # Gmail should be first (newer)

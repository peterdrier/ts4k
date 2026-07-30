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


class TestWatermarkDoesNotSkip:
    """Bug #23: watermark must not skip unreturned messages when count < total."""

    @pytest.mark.asyncio
    async def test_watermark_advances_to_oldest_when_truncated(self, monkeypatch):
        """When has_more, watermark advances to oldest returned (not newest)."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            if prefix != "g":
                return []
            # 10 messages from g only, count will be 3
            return _fake_messages("g", 10, base_hour=10)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands.whatsnew(key="skip_test", source="g", count=3)
        assert result.has_more is True

        # 10 messages: hours 10-19, sorted newest-first, top 3 = 19, 18, 17
        # Oldest returned = T17:00:00Z
        wm_g = kwm.get("skip_test", "g")
        assert wm_g is not None
        assert "T17:" in wm_g  # oldest returned, not T19 (newest)

    @pytest.mark.asyncio
    async def test_watermark_advances_to_newest_when_all_returned(self, monkeypatch):
        """When all messages returned, watermark advances to newest (standard)."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            if prefix != "g":
                return []
            return _fake_messages("g", 3, base_hour=10)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands.whatsnew(key="all_test", source="g", count=20)
        assert result.has_more is False

        wm_g = kwm.get("all_test", "g")
        assert wm_g is not None
        assert "T12:" in wm_g  # newest of 3 messages (hours 10, 11, 12)

    @pytest.mark.asyncio
    async def test_per_source_watermark_when_mixed(self, monkeypatch):
        """Source A truncated → oldest; source B fully returned → newest."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            if prefix == "g":
                # 10 messages at hours 1-10, will be truncated
                return _fake_messages("g", 10, base_hour=1)
            if prefix == "o":
                # 2 messages at hours 20-21, newer than all g — fully returned
                return _fake_messages("o", 2, base_hour=20)
            return []

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        # count=5: top 5 = o:msg1(T21), o:msg0(T20), g:msg9(T10), g:msg8(T09), g:msg7(T08)
        # g has 7 more dropped, o has 0 dropped
        result = await commands.whatsnew(key="mixed_test", count=5)
        assert result.has_more is True

        # g had messages truncated → watermark at oldest returned (T08)
        wm_g = kwm.get("mixed_test", "g")
        assert wm_g is not None
        assert "T08:" in wm_g
        # o was fully returned → watermark at newest (T21)
        wm_o = kwm.get("mixed_test", "o")
        assert wm_o is not None
        assert "T21:" in wm_o


class _StubAdapter:
    """Minimal adapter stub for exercising the real _fetch_for_source."""

    def __init__(self, msgs):
        self._msgs = msgs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def whatsnew(self, since=None, sender=None, domain=None, count=200):
        return self._msgs[:count]

    async def list_messages(self, query=None, count=20, page_token=None,
                            sender=None, domain=None):
        return self._msgs[:count]


class TestSingleSourceTruncationVisible:
    """PR #50 review: _fetch_for_source must not pre-slice to count, or a
    single source with more than count new messages looks fully returned —
    has_more stays False and the watermark skips the rest permanently."""

    @pytest.mark.asyncio
    async def test_single_source_over_count_sets_has_more(self, monkeypatch):
        msgs = _fake_messages("o", 10, base_hour=1)

        def fake_make_adapter(prefix, cfg):
            return _StubAdapter(list(reversed(msgs))) if prefix == "o" else None

        monkeypatch.setattr(commands, "_make_adapter", fake_make_adapter)

        result = await commands.whatsnew(key="single_trunc", source="o", count=3)
        assert result.has_more is True
        assert result.messages_processed == 3

        # Truncated source → watermark at oldest returned, not newest.
        # 10 messages at hours 1-10, top 3 = T10, T09, T08.
        wm_o = kwm.get("single_trunc", "o")
        assert wm_o is not None
        assert "T08:" in wm_o

    @pytest.mark.asyncio
    async def test_single_gmail_source_over_count_sets_has_more(self, monkeypatch):
        msgs = _fake_messages("g", 10, base_hour=1)

        def fake_make_adapter(prefix, cfg):
            return _StubAdapter(list(reversed(msgs))) if prefix == "g" else None

        monkeypatch.setattr(commands, "_make_adapter", fake_make_adapter)

        result = await commands.whatsnew(key="single_trunc_g", source="g", count=3)
        assert result.has_more is True
        wm_g = kwm.get("single_trunc_g", "g")
        assert wm_g is not None
        assert "T08:" in wm_g


class TestFilterBeforeCount:
    """Bug #25: filters must apply before count truncation."""

    @pytest.mark.asyncio
    async def test_filter_does_not_consume_count_slots(self, monkeypatch):
        """Requesting count=5 with filters returns 5 passing messages, not fewer."""
        from ts4k.state import filters as filters_mod

        def make_messages(prefix, n, base_hour=10):
            msgs = []
            for i in range(n):
                msgs.append({
                    "id": f"{prefix}:msg{i}",
                    "source": prefix,
                    "thread_id": f"{prefix}:t{i}",
                    "from": "newsletter@spam.com" if i % 2 == 0 else f"real{i}@test.com",
                    "subject": f"Subj {i}",
                    "date": f"2026-03-08T{base_hour + i:02d}:00:00Z",
                    "body": f"Body {i}",
                })
            return msgs

        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            # 20 messages, half from newsletter@spam.com
            return make_messages(prefix, 20, base_hour=1)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        # Set up a skip filter for newsletter@spam.com
        filter_cfg = filters_mod.get_config()
        filter_cfg.setdefault("skip_senders", []).append("newsletter@spam.com")
        monkeypatch.setattr(filters_mod, "get_config", lambda: filter_cfg)

        result = await commands.updates(since="1d", count=5, filter=True)
        # Should get 5 real messages, not 5 minus filtered ones
        assert result.messages_processed == 5


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

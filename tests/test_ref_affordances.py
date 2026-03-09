"""Tests for ref affordance hints — inline usage guidance in listing output."""

from __future__ import annotations

import argparse
import json

import pytest

from ts4k import commands
from ts4k.cli import _cmd_updates, _cmd_whatsnew, _cmd_list, _cmd_get, _refs_path
from ts4k.state.refs import RefTable


@pytest.fixture(autouse=True)
def mock_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))

    from ts4k import state as _state
    from ts4k.state import sources, stats, cache, keyed_watermarks as kwm

    monkeypatch.setattr(_state, "_current", None)
    monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(stats, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(stats, "_STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr(cache, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(kwm, "_CONFIG_DIR", tmp_path)

    cfg = {"g": {"provider": "gmail", "email": "t@t.com"}}
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


class TestUpdatesHint:
    @pytest.mark.asyncio
    async def test_updates_shows_get_hint(self, monkeypatch, capsys):
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 3)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        args = argparse.Namespace(source=None, since="1d", count=20, format="pipe", filter=False, key=None)
        await _cmd_updates(args)
        out = capsys.readouterr().out
        assert "→ ts4k get N to read message N" in out


class TestWhatsnewHint:
    @pytest.mark.asyncio
    async def test_whatsnew_shows_keyed_get_hint(self, monkeypatch, capsys):
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 3)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        args = argparse.Namespace(key="life", source=None, count=20, format="pipe", filter=False)
        await _cmd_whatsnew(args)
        out = capsys.readouterr().out
        assert "→ ts4k get -k life N to read message N" in out


class TestUpdatesWithKey:
    @pytest.mark.asyncio
    async def test_updates_with_key_saves_keyed_refs(self, monkeypatch, capsys, tmp_path):
        """updates -k saves refs under key and shows keyed hint."""
        from ts4k.cli import _refs_path

        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 3)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        args = argparse.Namespace(source=None, since="1d", count=20, format="pipe", filter=False, key="myquery")
        await _cmd_updates(args)
        out = capsys.readouterr().out
        assert "→ ts4k get -k myquery N to read message N" in out

        # Verify refs saved under key
        from ts4k.state.refs import RefTable
        rt = RefTable()
        rt.load(_refs_path("myquery"))
        assert rt.resolve("1") is not None

    @pytest.mark.asyncio
    async def test_updates_without_key_shows_global_hint(self, monkeypatch, capsys):
        """updates without -k shows global hint."""
        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _fake_messages(prefix, 3)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        args = argparse.Namespace(source=None, since="1d", count=20, format="pipe", filter=False, key=None)
        await _cmd_updates(args)
        out = capsys.readouterr().out
        assert "→ ts4k get N to read message N" in out
        assert "-k" not in out.split("→")[1]  # no -k in the hint


class TestListHint:
    @pytest.mark.asyncio
    async def test_list_shows_get_hint(self, monkeypatch, capsys):
        from ts4k.commands import CommandResult

        async def fake_list(**kwargs):
            return CommandResult(output="N|SOURCE|FROM|SUBJECT|DATE|SIZE\n1|g|s@t.com|Subj|12:00|6b")

        monkeypatch.setattr(commands, "list_messages", fake_list)

        args = argparse.Namespace(source=None, query=None, count=20, format="pipe", filter=False)
        await _cmd_list(args)
        out = capsys.readouterr().out
        assert "→ ts4k get N to read message N" in out


class TestGetRefError:
    @pytest.mark.asyncio
    async def test_get_wrong_key_suggests_global(self, capsys):
        """When ref exists in global but agent uses -k, suggest dropping -k."""
        # Save ref 1 in global table
        rt = RefTable()
        rt.assign([{"id": "g:abc123"}])
        rt.save(_refs_path(None))

        args = argparse.Namespace(id="1", key="life", format="pipe")
        with pytest.raises(SystemExit):
            await _cmd_get(args)
        out = capsys.readouterr().out
        assert "try: ts4k get 1" in out

    @pytest.mark.asyncio
    async def test_get_no_key_suggests_keyed_table(self, capsys):
        """When ref exists in a keyed table but agent uses no key, suggest -k."""
        # Save ref 1 in 'life' keyed table only
        rt = RefTable()
        rt.assign([{"id": "g:abc123"}])
        rt.save(_refs_path("life"))

        args = argparse.Namespace(id="1", key=None, format="pipe")
        with pytest.raises(SystemExit):
            await _cmd_get(args)
        out = capsys.readouterr().out
        assert "-k life" in out

    @pytest.mark.asyncio
    async def test_get_ref_not_found_anywhere(self, capsys):
        """When ref doesn't exist anywhere, show generic message."""
        args = argparse.Namespace(id="99", key=None, format="pipe")
        with pytest.raises(SystemExit):
            await _cmd_get(args)
        out = capsys.readouterr().out
        assert "Run 'whatsnew' or 'updates' first" in out

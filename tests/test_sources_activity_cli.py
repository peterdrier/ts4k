"""Tests for `ts4k sources` activity/note output and `ts4k src note`."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from ts4k import cli
from ts4k.state import cache, sources


def _args(action: str, prefix: str | None = None, provider: str | None = None,
          params: list[str] | None = None, text: list[str] | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        action=action, prefix=prefix, provider=provider, params=params, text=text,
    )


class TestSourcesListActivity:
    def test_empty_source_shows_empty_tag(self, ts4k_config, capsys):
        sources.add("oh", provider="o365", email="help@burn.camp")
        cli._cmd_sources(_args("list"))
        out = capsys.readouterr().out
        assert "activity: empty" in out

    def test_active_source_shows_count_and_newest(self, ts4k_config, capsys):
        sources.add("g", provider="gmail", email="a@b.com")
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "g:1", {"source": "g", "date": recent, "from": "a@b.com", "subject": "hi"}
        )
        cli._cmd_sources(_args("list"))
        out = capsys.readouterr().out
        assert "activity: active" in out
        assert "1 cached" in out
        assert recent[:10] in out

    def test_whatsapp_source_shows_na(self, ts4k_config, capsys):
        sources.add("w", provider="whatsapp", mcp_cwd="/path/to/wa")
        cli._cmd_sources(_args("list"))
        out = capsys.readouterr().out
        assert "activity: n/a" in out

    def test_custom_prefix_gmail_source_shows_na(self, ts4k_config, capsys):
        # "gw" is a Gmail source but isn't in cache.CACHEABLE_SOURCES, so
        # nothing ever lands in the cache for it — "empty" would wrongly
        # imply it was checked and found idle.
        sources.add("gw", provider="gmail", email="work@gmail.com")
        cli._cmd_sources(_args("list"))
        out = capsys.readouterr().out
        assert "activity: n/a" in out

    def test_dateless_cached_headers_do_not_crash(self, ts4k_config, capsys):
        sources.add("g", provider="gmail", email="a@b.com")
        cache.store_header("g:1", {"source": "g", "from": "a@b.com", "subject": "hi"})
        cli._cmd_sources(_args("list"))
        out = capsys.readouterr().out
        assert "activity: low" in out
        assert "1 cached" in out
        assert "newest unknown" in out

    def test_multiple_sources_activity_matches_individual_lookups(self, ts4k_config, capsys):
        sources.add("g", provider="gmail", email="a@b.com")
        sources.add("o", provider="o365", email="c@d.com")
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "g:1", {"source": "g", "date": recent, "from": "a@b.com", "subject": "hi"}
        )
        cli._cmd_sources(_args("list"))
        out = capsys.readouterr().out
        assert "g: gmail" in out
        assert "activity: active" in out
        assert "activity: empty" in out

    def test_note_shown_when_present(self, ts4k_config, capsys):
        sources.add("oh", provider="o365", email="help@burn.camp", note="mostly DMARC reports")
        cli._cmd_sources(_args("list"))
        out = capsys.readouterr().out
        assert "note: mostly DMARC reports" in out

    def test_note_omitted_when_absent(self, ts4k_config, capsys):
        sources.add("oh", provider="o365", email="help@burn.camp")
        cli._cmd_sources(_args("list"))
        out = capsys.readouterr().out
        assert "note:" not in out


class TestSrcNoteCommand:
    def test_sets_note(self, ts4k_config, capsys):
        sources.add("oh", provider="o365", email="help@burn.camp")
        cli._cmd_sources(_args("note", prefix="oh", text=["mostly", "DMARC", "reports"]))
        assert sources.get("oh")["note"] == "mostly DMARC reports"
        assert "Set note" in capsys.readouterr().out

    def test_clears_note_when_no_text(self, ts4k_config, capsys):
        sources.add("oh", provider="o365", email="help@burn.camp", note="old note")
        cli._cmd_sources(_args("note", prefix="oh", text=[]))
        assert "note" not in sources.get("oh")
        assert "Cleared note" in capsys.readouterr().out

    def test_missing_source_reports_not_found(self, ts4k_config, capsys):
        cli._cmd_sources(_args("note", prefix="nope", text=["hi"]))
        assert "not found" in capsys.readouterr().out

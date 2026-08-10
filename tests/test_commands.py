"""Tests for the ts4k command router (commands.py).

Verifies that command functions return strings (not print), handle contacts,
filters, and status without needing real adapters.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ts4k import commands
from ts4k.state import cache


# ---------------------------------------------------------------------------
# manage_contacts
# ---------------------------------------------------------------------------


class TestManageContacts:
    def test_link_and_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        # Force reload of module-level paths
        from ts4k.state import contacts as c
        monkeypatch.setattr(c, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(c, "_CONTACTS_FILE", tmp_path / "contacts.json")

        out = commands.manage_contacts(
            action="link", alias="alice", identifiers=["g:alice@test.com", "w:123"]
        )
        assert "alice" in out
        assert "g:alice@test.com" in out

        out = commands.manage_contacts(action="list")
        assert "alice" in out

    def test_find(self, tmp_path, monkeypatch):
        from ts4k.state import contacts as c
        monkeypatch.setattr(c, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(c, "_CONTACTS_FILE", tmp_path / "contacts.json")

        commands.manage_contacts(
            action="link", alias="bob", identifiers=["g:bob@test.com"]
        )
        out = commands.manage_contacts(action="find", term="bob")
        assert "bob" in out

        out = commands.manage_contacts(action="find", term="nonexistent")
        assert "No matches" in out

    def test_unlink(self, tmp_path, monkeypatch):
        from ts4k.state import contacts as c
        monkeypatch.setattr(c, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(c, "_CONTACTS_FILE", tmp_path / "contacts.json")

        commands.manage_contacts(
            action="link", alias="carol", identifiers=["g:carol@test.com"]
        )
        out = commands.manage_contacts(action="unlink", alias="carol")
        assert "(removed)" in out

    def test_link_missing_alias(self):
        out = commands.manage_contacts(action="link", alias=None, identifiers=["x"])
        assert "Error" in out

    def test_link_missing_identifiers(self):
        out = commands.manage_contacts(action="link", alias="x", identifiers=None)
        assert "Error" in out


# ---------------------------------------------------------------------------
# manage_filters
# ---------------------------------------------------------------------------


class TestManageFilters:
    def test_show_default(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        out = commands.manage_filters(action="show")
        assert "skip_senders" in out
        assert "(none)" in out

    def test_add_and_remove_sender(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        out = commands.manage_filters(action="add-sender", value="spam@test.com")
        assert "spam@test.com" in out

        out = commands.manage_filters(action="rm-sender", value="spam@test.com")
        assert "spam@test.com" not in out or "(empty)" in out

    def test_add_domain(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        out = commands.manage_filters(action="add-domain", value="junk.com")
        assert "junk.com" in out

    def test_reset(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        commands.manage_filters(action="add-sender", value="x@y.com")
        out = commands.manage_filters(action="reset")
        assert "reset" in out.lower()

    def test_skip_groups(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        out = commands.manage_filters(action="skip-groups", value="true")
        assert "True" in out


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_status_no_data(self, tmp_path, monkeypatch):
        """Status with no sources or stats should not crash."""
        from ts4k import state

        state.set_config_dir(tmp_path, reason="test")

        out = commands.get_status()
        assert "Sources:" in out
        assert "Contacts:" in out
        assert "Filters:" in out
        assert "Stats:" in out
        assert str(tmp_path) in out
        assert "(test)" in out
        assert isinstance(out, str)

        state.reset()


# ---------------------------------------------------------------------------
# CommandResult
# ---------------------------------------------------------------------------


class TestCommandResult:
    def test_default_values(self):
        r = commands.CommandResult()
        assert r.output == ""
        assert r.messages_processed == 0
        assert r.error is None
        assert r.ref_map is None

    def test_with_error(self):
        r = commands.CommandResult(error="bad thing")
        assert r.error == "bad thing"
        assert r.output == ""

    def test_with_ref_map(self):
        r = commands.CommandResult(ref_map={"g:abc": 1})
        assert r.ref_map == {"g:abc": 1}


# ---------------------------------------------------------------------------
# _resolve_ref
# ---------------------------------------------------------------------------


class TestResolveRef:
    def test_passthrough_real_id(self):
        from ts4k.state.refs import RefTable
        rt = RefTable()
        rt.assign([{"id": "g:abc"}])
        assert commands._resolve_ref("g:abc", rt) == "g:abc"

    def test_resolve_ref(self):
        from ts4k.state.refs import RefTable
        rt = RefTable()
        rt.assign([{"id": "g:abc"}])
        assert commands._resolve_ref("#1", rt) == "g:abc"

    def test_unresolvable_ref_passes_through(self):
        from ts4k.state.refs import RefTable
        rt = RefTable()
        assert commands._resolve_ref("#99", rt) == "#99"

    def test_resolve_bare_number(self):
        from ts4k.state.refs import RefTable
        rt = RefTable()
        rt.assign([{"id": "g:abc"}])
        assert commands._resolve_ref("1", rt) == "g:abc"

    def test_no_ref_table(self):
        assert commands._resolve_ref("#1", None) == "#1"
        assert commands._resolve_ref("1", None) == "1"
        assert commands._resolve_ref("g:abc", None) == "g:abc"


# ---------------------------------------------------------------------------
# source_activity
# ---------------------------------------------------------------------------


class TestSourceActivity:
    def test_no_cached_messages_is_empty(self, ts4k_config):
        result = commands.source_activity("g")
        assert result == {"count": 0, "newest": None, "tag": "empty"}

    def test_untracked_provider_is_na(self, ts4k_config):
        result = commands.source_activity("w", provider="whatsapp")
        assert result == {"count": 0, "newest": None, "tag": "n/a"}

    def test_untracked_calendar_provider_is_na(self, ts4k_config):
        result = commands.source_activity("gc", provider="gcal")
        assert result["tag"] == "n/a"

    def test_recent_messages_are_active(self, ts4k_config):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "g:1", {"source": "g", "date": recent, "from": "a@b.com", "subject": "hi"},
            provider="gmail",
        )
        result = commands.source_activity("g", provider="gmail")
        assert result["tag"] == "active"
        assert result["count"] == 1
        assert result["newest"] == recent

    def test_stale_messages_are_low(self, ts4k_config):
        old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "o:1", {"source": "o", "date": old, "from": "a@b.com", "subject": "hi"},
            provider="o365",
        )
        result = commands.source_activity("o", provider="o365")
        assert result["tag"] == "low"
        assert result["count"] == 1
        assert result["newest"] == old

    def test_only_counts_matching_source(self, ts4k_config):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "g:1", {"source": "g", "date": recent, "from": "a@b.com", "subject": "hi"},
            provider="gmail",
        )
        cache.store_header(
            "o:1", {"source": "o", "date": recent, "from": "c@d.com", "subject": "hi"},
            provider="o365",
        )
        result = commands.source_activity("g", provider="gmail")
        assert result["count"] == 1

    def test_activity_boundary_is_exactly_30_days(self, ts4k_config):
        just_inside = (datetime.now(timezone.utc) - timedelta(days=29)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "g:1", {"source": "g", "date": just_inside, "from": "a@b.com", "subject": "hi"},
            provider="gmail",
        )
        result = commands.source_activity("g", provider="gmail")
        assert result["tag"] == "active"

    def test_custom_prefix_gmail_is_cached(self, ts4k_config):
        # "gw" isn't the canonical "g" prefix, but its provider is gmail —
        # cache writes now gate on provider, not on the literal prefix
        # string, so activity for it is real, not "n/a". See issue #64.
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "gw:1", {"source": "gw", "date": recent, "from": "a@b.com", "subject": "hi"},
            provider="gmail",
        )
        result = commands.source_activity("gw", provider="gmail")
        assert result == {"count": 1, "newest": recent, "tag": "active"}

    def test_custom_prefix_o365_is_cached(self, ts4k_config):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "oh:1", {"source": "oh", "date": recent, "from": "a@b.com", "subject": "hi"},
            provider="o365",
        )
        result = commands.source_activity("oh", provider="o365")
        assert result == {"count": 1, "newest": recent, "tag": "active"}

    def test_dateless_headers_do_not_crash_and_still_count(self, ts4k_config):
        cache.store_header(
            "g:1", {"source": "g", "from": "a@b.com", "subject": "hi"}, provider="gmail"
        )
        result = commands.source_activity("g", provider="gmail")
        assert result["count"] == 1
        assert result["newest"] is None
        assert result["tag"] == "low"

    def test_preloaded_headers_match_per_source_lookup(self, ts4k_config):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "g:1", {"source": "g", "date": recent, "from": "a@b.com", "subject": "hi"},
            provider="gmail",
        )
        old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "o:1", {"source": "o", "date": old, "from": "c@d.com", "subject": "hi"},
            provider="o365",
        )

        groups = commands.cached_headers_by_source()
        via_group_g = commands.source_activity("g", provider="gmail", headers=groups.get("g", []))
        via_lookup_g = commands.source_activity("g", provider="gmail")
        assert via_group_g == via_lookup_g

        via_group_o = commands.source_activity("o", provider="o365", headers=groups.get("o", []))
        via_lookup_o = commands.source_activity("o", provider="o365")
        assert via_group_o == via_lookup_o
# get_message — readable-mode empty-body fallback (PR #60 round 4 fix)
# ---------------------------------------------------------------------------


class _MsgStubAdapter:
    """Stub adapter for get_message tests — returns a canned body keyed by
    the ``prefer_html`` flag, and records every call it receives."""

    def __init__(self, html_body: str, plain_body: str):
        self._html_body = html_body
        self._plain_body = plain_body
        self.calls: list[bool] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read_message(self, msg_id, prefer_html=False):
        self.calls.append(prefer_html)
        body = self._html_body if prefer_html else self._plain_body
        return {
            "id": msg_id,
            "source": "g",
            "from": "alice@test.com",
            "subject": "Test",
            "date": "2026-03-08T10:00:00Z",
            "body": body,
        }


class TestGetMessageReadableFallback:
    """Preferred HTML that normalizes to nothing (empty shell/tracking-only
    markup) must fall back to the plain-text alternative in readable mode,
    without affecting compact mode or non-empty readable results."""

    @pytest.fixture(autouse=True)
    def _sources(self, tmp_path):
        from ts4k import state
        state.set_config_dir(tmp_path, reason="test")
        (tmp_path / "sources.json").write_text(
            json.dumps({"g": {"provider": "gmail", "email": "t@t.com"}})
        )
        yield
        state.reset()

    @pytest.mark.asyncio
    async def test_empty_html_body_falls_back_to_plain(self, monkeypatch):
        stub = _MsgStubAdapter(
            html_body='<div style="display:none">tracking only</div>',
            plain_body="Real plain-text content.",
        )
        monkeypatch.setattr(commands, "_make_adapter", lambda prefix, cfg: stub)

        result = await commands.get_message("g:msg1", body_mode="readable")

        assert "Real plain-text content." in result.output
        # HTML tried first, then a single plain-text fallback fetch.
        assert stub.calls == [True, False]

    @pytest.mark.asyncio
    async def test_nonempty_readable_body_skips_fallback(self, monkeypatch):
        stub = _MsgStubAdapter(
            html_body="<p>Real HTML content.</p>",
            plain_body="Should never be fetched.",
        )
        monkeypatch.setattr(commands, "_make_adapter", lambda prefix, cfg: stub)

        result = await commands.get_message("g:msg1", body_mode="readable")

        assert "Real HTML content." in result.output
        assert stub.calls == [True]  # no fallback fetch when the body isn't empty

    @pytest.mark.asyncio
    async def test_compact_mode_unaffected(self, monkeypatch):
        """Guard: the readable-only fallback must never fire for compact
        mode, even when that body also normalizes to nothing."""
        stub = _MsgStubAdapter(
            html_body="<p>Unused HTML.</p>",
            plain_body='<div style="display:none">tracking only</div>',
        )
        monkeypatch.setattr(commands, "_make_adapter", lambda prefix, cfg: stub)

        result = await commands.get_message("g:msg1", body_mode="compact")

        assert result.output != ""  # header line still present, just no body
        assert stub.calls == [False]  # single fetch only, no fallback retry
class TestSkillReference:
    """Skill text is the agent's only self-documentation — pin what it must say."""

    def test_voice_notes_are_declared_transcribed(self):
        """Agents must not ask the user to listen to audio (ts4k#48).

        The bridge folds the transcript into the message body; nothing in the
        response shape says so, so the skill text is where an agent learns it.
        """
        text = commands.skill_reference("basic")
        assert "[voice" in text
        assert "transcript" in text.lower()

    def test_voice_guidance_survives_in_one_line(self):
        """It rides in every skill call — one line is the budget."""
        lines = [ln for ln in commands.skill_reference("basic").splitlines() if "[voice" in ln]
        assert len(lines) == 1, lines

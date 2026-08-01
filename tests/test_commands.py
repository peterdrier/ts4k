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
        from ts4k.state import contacts as c
        from ts4k.state import filters as f
        from ts4k.state import sources as src
        from ts4k.state import stats as st
        from ts4k.state import watermarks as wm

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
            "g:1", {"source": "g", "date": recent, "from": "a@b.com", "subject": "hi"}
        )
        result = commands.source_activity("g", provider="gmail")
        assert result["tag"] == "active"
        assert result["count"] == 1
        assert result["newest"] == recent

    def test_stale_messages_are_low(self, ts4k_config):
        old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "o:1", {"source": "o", "date": old, "from": "a@b.com", "subject": "hi"}
        )
        result = commands.source_activity("o", provider="o365")
        assert result["tag"] == "low"
        assert result["count"] == 1
        assert result["newest"] == old

    def test_only_counts_matching_source(self, ts4k_config):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "g:1", {"source": "g", "date": recent, "from": "a@b.com", "subject": "hi"}
        )
        cache.store_header(
            "o:1", {"source": "o", "date": recent, "from": "c@d.com", "subject": "hi"}
        )
        result = commands.source_activity("g", provider="gmail")
        assert result["count"] == 1

    def test_activity_boundary_is_exactly_30_days(self, ts4k_config):
        just_inside = (datetime.now(timezone.utc) - timedelta(days=29)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cache.store_header(
            "g:1", {"source": "g", "date": just_inside, "from": "a@b.com", "subject": "hi"}
        )
        result = commands.source_activity("g", provider="gmail")
        assert result["tag"] == "active"

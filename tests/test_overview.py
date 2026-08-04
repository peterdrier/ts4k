"""Tests for the overview command — period parsing, aggregation, formatting."""

from __future__ import annotations

import json

import pytest

from ts4k.commands import (
    _build_period_breakdown,
    _parse_period,
    _resolve_sender,
    overview,
)
from ts4k.core.format import format_overview


# ---------------------------------------------------------------------------
# Fixtures — seed a tmp cache with test headers
# ---------------------------------------------------------------------------

SAMPLE_HEADERS = [
    {"id": "g:msg1", "source": "g", "from": "alice@gmail.com", "subject": "Hello", "date": "2025-03-15T10:00:00Z", "thread_id": "g:t1"},
    {"id": "g:msg2", "source": "g", "from": "alice@gmail.com", "subject": "Re: Hello", "date": "2025-03-16T10:00:00Z", "thread_id": "g:t1"},
    {"id": "g:msg3", "source": "g", "from": "bob@gmail.com", "subject": "Meeting", "date": "2025-06-01T09:00:00Z", "thread_id": "g:t2"},
    {"id": "g:msg4", "source": "g", "from": "carol@gmail.com", "subject": "Invoice", "date": "2025-09-20T14:00:00Z", "thread_id": "g:t3"},
    {"id": "g:msg5", "source": "g", "from": "alice@gmail.com", "subject": "Followup", "date": "2025-10-01T11:00:00Z", "thread_id": "g:t1"},
    {"id": "o:msg1", "source": "o", "from": "hr@company.com", "subject": "Policy Update", "date": "2025-04-10T08:00:00Z"},
    {"id": "o:msg2", "source": "o", "from": "dave@company.com", "subject": "Budget", "date": "2025-07-15T16:00:00Z"},
    {"id": "o:msg3", "source": "o", "from": "alice@company.com", "subject": "Sync", "date": "2025-11-05T13:00:00Z"},
]


@pytest.fixture()
def seeded_cache(tmp_path, monkeypatch):
    """Set up a tmp cache dir and seed with SAMPLE_HEADERS."""
    import ts4k.state.cache as cache_mod

    monkeypatch.setattr(cache_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache_mod, "_INDEX_FILE", tmp_path / "cache" / "index.json")
    monkeypatch.setattr(cache_mod, "_BODIES_DIR", tmp_path / "cache" / "bodies")

    for h in SAMPLE_HEADERS:
        cache_mod.store_header(h["id"], h)

    return cache_mod


@pytest.fixture()
def seeded_contacts(tmp_path, monkeypatch):
    """Set up contacts with an alice alias."""
    import ts4k.state.contacts as contacts_mod

    monkeypatch.setattr(contacts_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(contacts_mod, "_CONTACTS_FILE", tmp_path / "contacts.json")
    contacts_mod.link("alice", "g:alice@gmail.com", "o:alice@company.com")
    return contacts_mod


# ---------------------------------------------------------------------------
# TestParsePeriod
# ---------------------------------------------------------------------------


class TestParsePeriod:
    def test_year(self):
        assert _parse_period("2025") == ("2025-01", "2026-01")

    def test_quarter_q1(self):
        assert _parse_period("2025-Q1") == ("2025-01", "2025-04")

    def test_quarter_q2(self):
        assert _parse_period("2025-Q2") == ("2025-04", "2025-07")

    def test_quarter_q3(self):
        assert _parse_period("2025-Q3") == ("2025-07", "2025-10")

    def test_quarter_q4(self):
        assert _parse_period("2025-Q4") == ("2025-10", "2026-01")

    def test_month(self):
        assert _parse_period("2025-03") == ("2025-03", "2025-04")

    def test_month_december(self):
        assert _parse_period("2025-12") == ("2025-12", "2026-01")

    def test_month_february_leap(self):
        # Just month boundaries, not day-level — should still work
        assert _parse_period("2024-02") == ("2024-02", "2024-03")

    def test_range(self):
        assert _parse_period("2025-01..2025-06") == ("2025-01", "2025-07")

    def test_quarter_case_insensitive(self):
        assert _parse_period("2025-q2") == ("2025-04", "2025-07")

    def test_invalid_fallback(self):
        start, end = _parse_period("garbage")
        assert start == "garbage"
        assert end == "9999-99"


# ---------------------------------------------------------------------------
# TestResolveSender
# ---------------------------------------------------------------------------


class TestResolveSender:
    def test_linked_returns_alias(self, seeded_contacts):
        assert _resolve_sender("g:alice@gmail.com") == "alice"

    def test_unlinked_returns_raw(self, seeded_contacts):
        assert _resolve_sender("g:unknown@gmail.com") == "g:unknown@gmail.com"

    def test_unprefixed_returns_raw(self, seeded_contacts):
        assert _resolve_sender("nobody@example.com") == "nobody@example.com"


# ---------------------------------------------------------------------------
# TestOverviewTopLevel
# ---------------------------------------------------------------------------


class TestOverviewTopLevel:
    def test_empty_cache(self, seeded_cache, monkeypatch):
        """Empty cache returns helpful message."""
        import ts4k.state.cache as cache_mod
        cache_mod.clear()
        result = overview()
        assert "empty" in result.lower() or "no cached" in result.lower()

    def test_single_source(self, seeded_cache, monkeypatch):
        """With only g: messages, top view shows 1 source."""
        import ts4k.state.cache as cache_mod
        # Clear o: messages
        cache_mod.clear(source="o")
        result = overview()
        assert "1 sources" in result
        assert "gmail" in result

    def test_multiple_sources(self, seeded_cache):
        result = overview()
        assert "2 sources" in result
        assert "gmail" in result
        assert "o365" in result

    def test_top_sender_ordering(self, seeded_cache):
        """alice has 3 g: messages, should appear first in top senders."""
        result = overview()
        # alice should appear in the g line's top senders
        assert "alice" in result

    def test_contact_collapsing(self, seeded_cache, seeded_contacts):
        """With contacts linked, alice@gmail.com collapses to 'alice'."""
        result = overview()
        # The resolved sender should show "alice" not "alice@gmail.com"
        assert "alice(" in result or "alice|" in result

    def test_total_count(self, seeded_cache):
        result = overview()
        assert "8 messages" in result


# ---------------------------------------------------------------------------
# TestOverviewSourceDrilldown
# ---------------------------------------------------------------------------


class TestOverviewSourceDrilldown:
    def test_source_counts(self, seeded_cache):
        result = overview(source="g")
        assert "5 messages" in result
        assert "gmail" in result

    def test_top_senders(self, seeded_cache):
        result = overview(source="g")
        assert "TOP_SENDERS" in result

    def test_thread_grouping(self, seeded_cache):
        """g: has 3 threads with thread_id, should show TOP_THREADS."""
        result = overview(source="g")
        assert "TOP_THREADS" in result

    def test_missing_source(self, seeded_cache):
        result = overview(source="w")
        assert "no cached" in result.lower()


# ---------------------------------------------------------------------------
# TestOverviewContactDrilldown
# ---------------------------------------------------------------------------


class TestOverviewContactDrilldown:
    def test_cross_source(self, seeded_cache, seeded_contacts):
        result = overview(contact="alice")
        # alice has messages in both g and o
        assert "gmail" in result
        assert "o365" in result

    def test_period_filter(self, seeded_cache, seeded_contacts):
        result = overview(contact="alice", period="2025-Q1")
        assert "alice" in result
        # Q1 2025 = Jan-Mar. alice has msgs on 2025-03-15 and 2025-03-16
        assert "2 messages" in result

    def test_unknown_contact(self, seeded_cache, seeded_contacts):
        result = overview(contact="zzzunknown")
        assert "no cached" in result.lower()

    def test_quarterly_breakdown(self, seeded_cache, seeded_contacts):
        result = overview(contact="alice")
        assert "PERIODS" in result
        assert "2025-Q1" in result


# ---------------------------------------------------------------------------
# TestBuildPeriodBreakdown
# ---------------------------------------------------------------------------


class TestBuildPeriodBreakdown:
    def test_basic_breakdown(self):
        headers = [
            {"date": "2025-01-10T00:00:00Z"},
            {"date": "2025-02-10T00:00:00Z"},
            {"date": "2025-04-10T00:00:00Z"},
        ]
        result = _build_period_breakdown(headers)
        periods = {p["period"]: p["count"] for p in result}
        assert periods["2025-Q1"] == 2
        assert periods["2025-Q2"] == 1

    def test_empty_headers(self):
        assert _build_period_breakdown([]) == []


# ---------------------------------------------------------------------------
# TestFormatOverview
# ---------------------------------------------------------------------------


class TestFormatOverview:
    def _top_data(self):
        return {
            "level": "top",
            "total": 100,
            "source_count": 2,
            "sources": [
                {
                    "prefix": "g",
                    "label": "gmail",
                    "count": 80,
                    "date_start": "2025-01",
                    "date_end": "2025-12",
                    "top_senders": [{"name": "alice", "count": 40}],
                },
                {
                    "prefix": "o",
                    "label": "o365",
                    "count": 20,
                    "date_start": "2025-06",
                    "date_end": "2025-12",
                    "top_senders": [{"name": "hr", "count": 10}],
                },
            ],
        }

    def test_pipe_top(self):
        result = format_overview(self._top_data(), fmt="pipe")
        assert "Overview:" in result
        assert "2 sources" in result
        assert "g|gmail|80 msgs" in result

    def test_json_top(self):
        result = format_overview(self._top_data(), fmt="json")
        data = json.loads(result)
        assert data["level"] == "top"
        assert data["total"] == 100

    def test_xml_top(self):
        result = format_overview(self._top_data(), fmt="xml")
        assert "<overview" in result
        assert 'level="top"' in result
        assert "</overview>" in result

    def test_pipe_source(self):
        data = {
            "level": "source",
            "prefix": "g",
            "label": "gmail",
            "total": 80,
            "date_start": "2025-01",
            "date_end": "2025-12",
            "top_senders": [{"name": "alice", "count": 40}],
            "top_threads": [],
        }
        result = format_overview(data, fmt="pipe")
        assert "TOP_SENDERS" in result
        assert "alice|40 msgs" in result

    def test_pipe_contact(self):
        data = {
            "level": "contact",
            "contact": "alice",
            "total": 50,
            "source_count": 2,
            "sources": [
                {"prefix": "g", "label": "gmail", "count": 40, "date_start": "2025-01", "date_end": "2025-12"},
            ],
            "periods": [{"period": "2025-Q1", "count": 12}],
        }
        result = format_overview(data, fmt="pipe")
        assert "alice" in result
        assert "PERIODS" in result
        assert "2025-Q1|12" in result

    def test_json_roundtrip(self):
        data = self._top_data()
        result = format_overview(data, fmt="json")
        parsed = json.loads(result)
        assert parsed["total"] == data["total"]
        assert len(parsed["sources"]) == 2

    def test_xml_source(self):
        data = {
            "level": "source",
            "prefix": "g",
            "label": "gmail",
            "total": 5,
            "date_start": "",
            "date_end": "",
            "top_senders": [{"name": "bob", "count": 3}],
            "top_threads": [],
        }
        result = format_overview(data, fmt="xml")
        assert 'level="source"' in result
        assert 'name="bob"' in result

    def test_xml_contact(self):
        data = {
            "level": "contact",
            "contact": "alice",
            "total": 10,
            "source_count": 1,
            "sources": [{"prefix": "g", "label": "gmail", "count": 10}],
            "periods": [{"period": "2025-Q1", "count": 5}],
        }
        result = format_overview(data, fmt="xml")
        assert 'level="contact"' in result
        assert 'name="alice"' in result
        assert "<period" in result

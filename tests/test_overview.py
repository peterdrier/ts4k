"""Tests for the overview command — period parsing, aggregation, formatting."""

from __future__ import annotations

import json

import pytest

from ts4k.commands import (
    _build_period_breakdown,
    _parse_period,
    _resolve_sender,
    _sibling_prefixes,
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
        provider = "gmail" if h["source"] == "g" else "o365"
        cache_mod.store_header(h["id"], h, provider=provider)

    return cache_mod


@pytest.fixture()
def seeded_meters(tmp_path, monkeypatch):
    """Isolate the meters state file (#31 HTTP notification sources)."""
    import ts4k.state.meters as meters_mod

    monkeypatch.setattr(meters_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(meters_mod, "_METERS_FILE", tmp_path / "meters.json")
    return meters_mod


@pytest.fixture()
def seeded_sources(tmp_path, monkeypatch):
    """Isolate the sources config file (#31 review — meters must be
    filtered to currently-configured HTTP prefixes)."""
    import ts4k.state.sources as sources_mod

    monkeypatch.setattr(sources_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources_mod, "_SOURCES_FILE", tmp_path / "sources.json")
    return sources_mod


@pytest.fixture()
def empty_cache(tmp_path, monkeypatch):
    """Set up a tmp cache dir with no messages — unlike ``seeded_cache``,
    which always seeds eight unrelated messages and would mask a bug that
    only shows up when the cache is genuinely empty (#31 review — F2)."""
    import ts4k.state.cache as cache_mod

    monkeypatch.setattr(cache_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache_mod, "_INDEX_FILE", tmp_path / "cache" / "index.json")
    monkeypatch.setattr(cache_mod, "_BODIES_DIR", tmp_path / "cache" / "bodies")
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

    def test_custom_prefix_resolves_via_the_message_own_source(self, ts4k_config):
        """A contact imported under a custom-prefixed source (e.g. "gw" for
        a second Gmail account) must resolve for a message whose own
        `source` is that same prefix — the static g/o/w letters alone
        never see it."""
        from ts4k.state import contacts

        contacts.link("alice", "gw:alice@x.com")
        assert _resolve_sender("alice@x.com", "gw") == "alice"

    def test_canonical_prefixes_still_resolve_without_a_source(self, ts4k_config):
        """No source/sibling_prefixes given — must still fall back to the
        canonical letters, e.g. for direct/standalone callers."""
        from ts4k.state import contacts

        contacts.link("alice", "g:alice@gmail.com")
        assert _resolve_sender("alice@gmail.com") == "alice"

    def test_sibling_source_of_the_same_provider_resolves(self, ts4k_config):
        """A message arriving under one configured Gmail source ("g") must
        still resolve a contact imported under a sibling Gmail source
        ("gw") of the same provider type."""
        from ts4k.state import contacts, sources

        sources.add("g", provider="gmail", email="a@gmail.com")
        sources.add("gw", provider="gmail", email="b@gmail.com")
        contacts.link("alice", "gw:alice@x.com")

        siblings = _sibling_prefixes(sources.list_all())
        assert _resolve_sender("alice@x.com", "g", siblings) == "alice"


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
# TestOverviewMeters (#31 — HTTP notification source meter snapshots)
# ---------------------------------------------------------------------------


class TestOverviewMeters:
    def test_meters_attach_to_a_cached_source(self, seeded_cache, seeded_meters):
        seeded_meters.set_meters("g", [{"label": "Something", "count": 2}])
        result = overview()
        assert "meter: Something=2" in result

    def test_meters_only_source_still_appears(self, seeded_cache, seeded_meters, seeded_sources):
        """An HTTP source has no cached messages (it polls live, like
        WhatsApp) but should still show up for its live meter snapshot,
        as long as it's still configured — see TestOverviewMetersStaleness
        for the case where it isn't (#31 review — F3)."""
        seeded_sources.add("h", provider="http", url="https://example.com/api/notifications")
        seeded_meters.set_meters(
            "h", [{"label": "Board votes needed", "count": 5, "link": "/OnboardingReview/BoardVoting"}]
        )
        result = overview()
        assert "meter: Board votes needed=5 (/OnboardingReview/BoardVoting)" in result

    def test_no_meters_no_output(self, seeded_cache):
        result = overview()
        assert "meter:" not in result


# ---------------------------------------------------------------------------
# TestOverviewMetersStaleness (#31 review — F3: stale meter snapshots)
# ---------------------------------------------------------------------------


class TestOverviewMetersStaleness:
    """``src rm`` doesn't touch meters.json, so overview() must filter
    saved snapshots to currently-configured HTTP prefixes rather than
    trusting the file blindly — otherwise a removed (or repurposed)
    source's stale counts show up forever."""

    def test_meter_hidden_for_never_configured_prefix(self, seeded_cache, seeded_meters):
        seeded_meters.set_meters("h", [{"label": "Board votes needed", "count": 5}])
        result = overview()
        assert "meter:" not in result

    def test_meter_hidden_after_source_removed(self, seeded_cache, seeded_meters, seeded_sources):
        seeded_sources.add("h", provider="http", url="https://example.com/api/notifications")
        seeded_meters.set_meters("h", [{"label": "Board votes needed", "count": 5}])
        assert "meter:" in overview()  # sanity check: shows while configured

        seeded_sources.remove("h")
        assert "meter:" not in overview()

    def test_meter_hidden_when_prefix_repurposed_to_another_provider(
        self, seeded_cache, seeded_meters, seeded_sources
    ):
        """A stale snapshot under a reused prefix must not leak onto an
        unrelated, non-HTTP source now using that prefix."""
        seeded_meters.set_meters("h", [{"label": "Board votes needed", "count": 5}])
        seeded_sources.add("h", provider="gmail", email="h@example.com")
        result = overview()
        assert "meter:" not in result


# ---------------------------------------------------------------------------
# TestOverviewMetersEmptyCache (#31 review — F2: HTTP-only setup)
# ---------------------------------------------------------------------------


class TestOverviewMetersEmptyCache:
    """overview() used to return the cache-empty message before ever
    reaching the meters loop, so an HTTP-only setup — no cached messages
    at all, since HTTP sources poll live — never saw its meters."""

    def test_meters_render_with_no_cached_messages(self, empty_cache, seeded_meters, seeded_sources):
        seeded_sources.add("h", provider="http", url="https://example.com/api/notifications")
        seeded_meters.set_meters("h", [{"label": "Board votes needed", "count": 5}])
        result = overview()
        assert "meter: Board votes needed=5" in result

    def test_cache_empty_message_when_truly_nothing(self, empty_cache, seeded_sources):
        result = overview()
        assert "empty" in result.lower()


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

    def test_pipe_top_with_meters(self):
        data = self._top_data()
        data["sources"][0]["meters"] = [{"label": "Board votes needed", "count": 5, "link": "/x"}]
        result = format_overview(data, fmt="pipe")
        assert "meter: Board votes needed=5 (/x)" in result

    def test_xml_top_with_meters(self):
        data = self._top_data()
        data["sources"][0]["meters"] = [{"label": "Board votes needed", "count": 5}]
        result = format_overview(data, fmt="xml")
        assert 'meters="Board votes needed(5)"' in result

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

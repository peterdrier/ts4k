"""Listing filters must stack with --since (ts4k#105).

Before the fix, ``list_messages`` silently dropped ``query`` when *since*
was set (the time-aware path never received it), and ``--domain`` passed
vacuously through sources whose senders have no email address (WhatsApp),
so those messages dominated the results.
"""

from __future__ import annotations

import json

import pytest

from ts4k import commands
from ts4k.commands import _matches_post_filters


class _RecordingStub:
    """Stub adapter returning canned entries, recording call kwargs."""

    def __init__(self, messages):
        self._messages = messages
        self.whatsnew_calls: list[dict] = []
        self.list_calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def whatsnew(self, since=None, sender=None, domain=None, count=200):
        self.whatsnew_calls.append(
            {"since": since, "sender": sender, "domain": domain, "count": count}
        )
        return self._messages

    async def list_messages(self, query=None, count=20, page_token=None,
                            sender=None, domain=None):
        self.list_calls.append(
            {"query": query, "count": count, "sender": sender, "domain": domain}
        )
        return self._messages


O365_MSGS = [
    {"id": "o:1", "source": "o", "from": "Jan <jan@vdpadvies.nl>",
     "subject": "VDP proposal", "date": "2026-08-20T09:00:00Z",
     "snippet": "the VDP figures"},
    {"id": "o:2", "source": "o", "from": "Robot <noreply@namecheap.com>",
     "subject": "Renewal notice", "date": "2026-08-21T09:00:00Z",
     "snippet": "your domain expires"},
]

WA_MSGS = [
    {"id": "w:1", "source": "w", "from": "Family Group",
     "subject": "Family Group", "date": "2026-08-22T09:00:00Z",
     "body": "dinner sunday?"},
]


@pytest.fixture
def _sources(tmp_path):
    from ts4k import state
    state.set_config_dir(tmp_path, reason="test")
    yield tmp_path
    state.reset()


def _write_sources(tmp_path, cfg):
    (tmp_path / "sources.json").write_text(json.dumps(cfg))


class TestQueryStacksWithSince:
    @pytest.mark.asyncio
    async def test_query_filters_non_gmail_since_path(self, _sources, monkeypatch):
        _write_sources(_sources, {"o": {"provider": "o365", "client_id": "x"}})
        stub = _RecordingStub(O365_MSGS)
        monkeypatch.setattr(commands, "_make_adapter", lambda prefix, cfg: stub)

        result = await commands.list_messages(
            source="o", query="VDP", since="2026-06-01", count=5
        )

        assert "VDP proposal" in result.output
        assert "Renewal notice" not in result.output
        assert stub.whatsnew_calls  # took the time-aware path

    @pytest.mark.asyncio
    async def test_query_rides_gmail_server_side_search(self, _sources, monkeypatch):
        _write_sources(_sources, {"g": {"provider": "gmail", "email": "a@b.c"}})
        stub = _RecordingStub([])
        monkeypatch.setattr(commands, "_make_adapter", lambda prefix, cfg: stub)

        await commands.list_messages(
            source="g", query="VDP", since="2026-06-01", count=5
        )

        assert len(stub.list_calls) == 1
        sent_query = stub.list_calls[0]["query"]
        assert "VDP" in sent_query
        assert "after:" in sent_query  # time bound still applied

    @pytest.mark.asyncio
    async def test_github_query_uses_native_search(self, _sources, monkeypatch):
        # GitHub's query is search syntax, not a header substring — it must
        # go through list_messages, with the time bound applied client-side.
        _write_sources(_sources, {"gh": {"provider": "github", "token": "t"}})
        stub = _RecordingStub([
            {"id": "gh:1", "source": "gh", "from": "octocat",
             "subject": "old issue", "date": "2025-01-01T00:00:00Z"},
            {"id": "gh:2", "source": "gh", "from": "octocat",
             "subject": "new issue", "date": "2026-08-20T00:00:00Z"},
        ])
        monkeypatch.setattr(commands, "_make_adapter", lambda prefix, cfg: stub)

        result = await commands.list_messages(
            source="gh", query="repo:owner/name is:open",
            since="2026-06-01", count=5,
        )

        assert stub.list_calls and stub.list_calls[0]["query"] == "repo:owner/name is:open"
        assert not stub.whatsnew_calls
        assert "new issue" in result.output
        assert "old issue" not in result.output  # since bound applied client-side

    @pytest.mark.asyncio
    async def test_whatsapp_query_uses_native_search(self, _sources, monkeypatch):
        # WhatsApp's list_messages defines its own query semantics —
        # chat:<jid> lookups and bridge-side content search. A chat: query
        # substring-matched against headers would return nothing, so with
        # --since it must still route through list_messages.
        _write_sources(_sources, {"w": {"provider": "whatsapp"}})
        stub = _RecordingStub([
            {"id": "w:1", "source": "w", "from": "Family Group",
             "subject": "Family Group", "date": "2025-01-01T00:00:00Z",
             "body": "old message"},
            {"id": "w:2", "source": "w", "from": "Family Group",
             "subject": "Family Group", "date": "2026-08-22T09:00:00Z",
             "body": "dinner sunday?"},
        ])
        monkeypatch.setattr(commands, "_make_adapter", lambda prefix, cfg: stub)

        result = await commands.list_messages(
            source="w", query="chat:123@s.whatsapp.net",
            since="2026-06-01", count=5,
        )

        assert stub.list_calls
        assert stub.list_calls[0]["query"] == "chat:123@s.whatsapp.net"
        assert not stub.whatsnew_calls
        assert "dinner sunday" in result.output
        assert "old message" not in result.output  # since bound client-side

    @pytest.mark.asyncio
    async def test_no_query_leaves_since_path_unfiltered(self, _sources, monkeypatch):
        _write_sources(_sources, {"o": {"provider": "o365", "client_id": "x"}})
        stub = _RecordingStub(O365_MSGS)
        monkeypatch.setattr(commands, "_make_adapter", lambda prefix, cfg: stub)

        result = await commands.list_messages(source="o", since="2026-06-01", count=5)

        assert "VDP proposal" in result.output
        assert "Renewal notice" in result.output


class TestDomainStacksWithSince:
    @pytest.mark.asyncio
    async def test_domain_excludes_sources_without_email_addresses(
        self, _sources, monkeypatch
    ):
        _write_sources(_sources, {
            "o": {"provider": "o365", "client_id": "x"},
            "w": {"provider": "whatsapp"},
        })
        stubs = {"o": _RecordingStub(O365_MSGS), "w": _RecordingStub(WA_MSGS)}
        monkeypatch.setattr(
            commands, "_make_adapter", lambda prefix, cfg: stubs[prefix]
        )

        result = await commands.list_messages(
            domain="vdpadvies.nl", since="2026-01-01", count=30
        )

        assert "VDP proposal" in result.output
        assert "Renewal notice" not in result.output  # wrong domain
        assert "dinner sunday" not in result.output   # WhatsApp: no email address

    @pytest.mark.asyncio
    async def test_sender_backstop_on_since_path(self, _sources, monkeypatch):
        _write_sources(_sources, {"w": {"provider": "whatsapp"}})
        stub = _RecordingStub(WA_MSGS)
        monkeypatch.setattr(commands, "_make_adapter", lambda prefix, cfg: stub)

        result = await commands.list_messages(
            sender="jan@vdpadvies.nl", since="2026-01-01", count=10
        )

        # WhatsApp ignores sender server-side; the backstop must drop it.
        assert result.error == "No new messages."


class TestResolveSinceToUtc:
    """ISO --since values with a UTC offset must be normalized before the
    (lexical) comparisons against Z-normalized message dates."""

    def test_offset_converted_to_utc(self):
        assert commands._resolve_since_to_utc(
            "2026-08-20T12:00:00-04:00"
        ) == "2026-08-20T16:00:00Z"

    def test_z_and_naive_pass_through_equivalent(self):
        assert commands._resolve_since_to_utc(
            "2026-08-20T12:00:00Z"
        ) == "2026-08-20T12:00:00Z"
        assert commands._resolve_since_to_utc("2026-08-20") == "2026-08-20"

    def test_relative_and_all_unchanged(self):
        assert commands._resolve_since_to_utc("all") is None
        out = commands._resolve_since_to_utc("1d")
        assert out is not None and out.endswith("Z")


class TestMatchesPostFilters:
    MSG = {"from": "jan@vdpadvies.nl", "subject": "VDP proposal",
           "snippet": "quarterly figures"}

    def test_no_filters_passes(self):
        assert _matches_post_filters(self.MSG)

    def test_domain_matches_and_subdomain(self):
        assert _matches_post_filters(self.MSG, domain="vdpadvies.nl")
        assert _matches_post_filters(
            {"from": "x@mail.vdpadvies.nl"}, domain="vdpadvies.nl"
        )
        assert not _matches_post_filters(self.MSG, domain="advies.nl")

    def test_domain_requires_suffix_not_substring(self):
        # "advies.nl" is a substring of the address but not its domain suffix
        # boundary — must not match a different registrable domain.
        assert not _matches_post_filters(
            {"from": "x@vdpadvies.nl.evil.com"}, domain="vdpadvies.nl"
        )

    def test_domain_excludes_plain_names(self):
        assert not _matches_post_filters({"from": "Family Group"}, domain="vdpadvies.nl")

    def test_domain_excludes_hostname_like_names_without_address(self):
        # An HTTP source named like a hostname ends in ".domain" but has no
        # email address — must not match.
        assert not _matches_post_filters(
            {"from": "alerts.example.com"}, domain="example.com"
        )

    def test_query_matches_subject_snippet_from(self):
        assert _matches_post_filters(self.MSG, query="vdp")
        assert _matches_post_filters(self.MSG, query="figures")
        assert not _matches_post_filters(self.MSG, query="unrelated")

    def test_sender_match(self):
        assert _matches_post_filters(self.MSG, sender="jan@vdpadvies.nl")
        assert not _matches_post_filters(self.MSG, sender="piet@vdpadvies.nl")

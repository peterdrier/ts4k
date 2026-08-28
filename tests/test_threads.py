# tests/test_threads.py
"""Tests for thread-level operations (#20).

Covers the Gmail threads.modify adapter path, thread-collapsed listings,
ref resolution to threads, and the non-Gmail error surface.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
from ts4k.commands import manage_message
from ts4k.core.format import collapse_threads, format_listing, format_thread_listing
from ts4k.state.refs import RefTable


# ---------------------------------------------------------------------------
# Test data — one 4-message thread plus a standalone message
# ---------------------------------------------------------------------------

# From-fields are bare addresses: normalize_headers strips display names
# before any of this runs.  Newest first, as listings are sorted.
THREAD_MESSAGES = [
    {
        "id": "g:m4",
        "thread_id": "g:t1",
        "from": "alice@acme.com",
        "subject": "Re: Q3 planning",
        "date": "2026-02-20T16:00:00Z",
        "snippet": "Agreed, let's lock the room for Thursday.",
        "source": "g",
        "unread": True,
    },
    {
        "id": "g:m3",
        "thread_id": "g:t1",
        "from": "carol@acme.com",
        "subject": "Re: Q3 planning",
        "date": "2026-02-19T11:30:00Z",
        "snippet": "I can join after 2pm.",
        "source": "g",
    },
    {
        "id": "g:m2",
        "thread_id": "g:t1",
        "from": "bob@corp.com",
        "subject": "Re: Q3 planning",
        "date": "2026-02-18T09:05:00Z",
        "snippet": "Numbers attached.",
        "source": "g",
    },
    {
        "id": "g:m1",
        "thread_id": "g:t1",
        "from": "alice@acme.com",
        "subject": "Q3 planning",
        "date": "2026-02-18T08:00:00Z",
        "snippet": "Kicking this off.",
        "source": "g",
    },
    {
        "id": "g:solo",
        "thread_id": "g:t2",
        "from": "newsletter@example.com",
        "subject": "Weekly digest",
        "date": "2026-02-17T06:00:00Z",
        "snippet": "This week in widgets.",
        "source": "g",
    },
]


@pytest.fixture
def gmail_modify():
    """GmailAdapter at modify level with a mocked service."""
    adapter = GmailAdapter(
        GmailAdapterConfig(user_email="test@gmail.com", level="modify"),
        prefix="g",
    )
    adapter._service = MagicMock()
    return adapter


@pytest.fixture
def gmail_source(monkeypatch, tmp_path):
    """Isolated config dir with a single Gmail source at modify level."""
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
    from ts4k.state import cache, sources, stats

    monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(stats, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(stats, "_STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr(cache, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "_INDEX_FILE", tmp_path / "cache" / "index.json")
    monkeypatch.setattr(cache, "_BODIES_DIR", tmp_path / "cache" / "bodies")
    sources.add("g", provider="gmail", email="a@b.com", level="modify")
    return sources


# ---------------------------------------------------------------------------
# Adapter — threads.modify
# ---------------------------------------------------------------------------


class TestGmailModifyThread:
    @pytest.mark.asyncio
    async def test_archive_removes_inbox_from_whole_thread(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().threads().modify().execute.return_value = {"id": "t1"}

        result = await gmail_modify.modify_thread("g:t1", "archive")

        svc.users().threads().modify.assert_called_with(
            userId="me", id="t1", body={"removeLabelIds": ["INBOX"]},
        )
        assert result == {"id": "g:t1", "status": "archived"}

    @pytest.mark.asyncio
    async def test_archive_is_one_api_call(self, gmail_modify):
        """One threads.modify — never a per-message loop."""
        svc = gmail_modify._service
        svc.users().threads().modify().execute.return_value = {"id": "t1"}
        svc.reset_mock()

        await gmail_modify.modify_thread("g:t1", "archive")

        assert svc.users().threads().modify.call_count == 1
        assert svc.users().messages().modify.call_count == 0

    @pytest.mark.asyncio
    async def test_unarchive_adds_inbox(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().threads().modify().execute.return_value = {"id": "t1"}

        result = await gmail_modify.modify_thread("g:t1", "unarchive")

        svc.users().threads().modify.assert_called_with(
            userId="me", id="t1", body={"addLabelIds": ["INBOX"]},
        )
        assert result["status"] == "unarchived"

    @pytest.mark.asyncio
    async def test_label_resolves_and_applies_to_thread(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().labels().list().execute.return_value = {
            "labels": [{"id": "Label_7", "name": "llm-garbage"}]
        }
        svc.users().threads().modify().execute.return_value = {"id": "t1"}

        result = await gmail_modify.modify_thread("g:t1", "label", "llm-garbage")

        svc.users().threads().modify.assert_called_with(
            userId="me", id="t1", body={"addLabelIds": ["Label_7"]},
        )
        assert result["status"] == "labeled"
        assert result["label"] == "llm-garbage"

    @pytest.mark.asyncio
    async def test_unlabel_removes_from_thread(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().labels().list().execute.return_value = {
            "labels": [{"id": "Label_7", "name": "llm-garbage"}]
        }
        svc.users().threads().modify().execute.return_value = {"id": "t1"}

        result = await gmail_modify.modify_thread("g:t1", "unlabel", "llm-garbage")

        svc.users().threads().modify.assert_called_with(
            userId="me", id="t1", body={"removeLabelIds": ["Label_7"]},
        )
        assert result["status"] == "unlabeled"

    @pytest.mark.asyncio
    async def test_label_without_name_raises(self, gmail_modify):
        with pytest.raises(ValueError, match="requires a label"):
            await gmail_modify.modify_thread("g:t1", "label")

    @pytest.mark.asyncio
    async def test_read_and_unread(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().threads().modify().execute.return_value = {"id": "t1"}

        assert (await gmail_modify.modify_thread("g:t1", "read"))["status"] == "marked_read"
        svc.users().threads().modify.assert_called_with(
            userId="me", id="t1", body={"removeLabelIds": ["UNREAD"]},
        )

        assert (await gmail_modify.modify_thread("g:t1", "unread"))["status"] == "marked_unread"
        svc.users().threads().modify.assert_called_with(
            userId="me", id="t1", body={"addLabelIds": ["UNREAD"]},
        )

    @pytest.mark.asyncio
    async def test_trash_uses_threads_trash(self, gmail_modify):
        svc = gmail_modify._service
        svc.users().threads().trash().execute.return_value = {"id": "t1"}

        result = await gmail_modify.modify_thread("g:t1", "trash")

        svc.users().threads().trash.assert_called_with(userId="me", id="t1")
        assert result["status"] == "trashed"

    @pytest.mark.asyncio
    async def test_unknown_action_raises(self, gmail_modify):
        with pytest.raises(ValueError, match="not supported at thread level"):
            await gmail_modify.modify_thread("g:t1", "move")

    @pytest.mark.asyncio
    async def test_blocked_at_readonly(self):
        adapter = GmailAdapter(
            GmailAdapterConfig(user_email="test@gmail.com"), prefix="g",
        )
        adapter._service = MagicMock()
        with pytest.raises(PermissionError, match="level='modify'"):
            await adapter.modify_thread("g:t1", "archive")


# ---------------------------------------------------------------------------
# manage --thread
# ---------------------------------------------------------------------------


class TestManageThread:
    @pytest.mark.asyncio
    async def test_archive_thread_resolves_message_to_thread(self, gmail_source):
        """A ref pointing at a message archives that message's thread."""
        mock_adapter = AsyncMock()
        mock_adapter.modify_thread.return_value = {"id": "g:t1", "status": "archived"}
        mock_adapter.read_message.return_value = {"id": "g:m4", "thread_id": "g:t1"}

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter), \
                patch("ts4k.commands.cache.get_message", return_value=None):
            result = await manage_message(action="archive", msg_id="g:m4", thread=True)

        mock_adapter.modify_thread.assert_awaited_once_with("g:t1", "archive", None)
        assert "archived" in result

    @pytest.mark.asyncio
    async def test_thread_id_comes_from_cache_without_a_read(self, gmail_source):
        mock_adapter = AsyncMock()
        mock_adapter.modify_thread.return_value = {"id": "g:t1", "status": "archived"}

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter), \
                patch("ts4k.commands.cache.get_message",
                      return_value={"id": "g:m4", "thread_id": "g:t1"}):
            await manage_message(action="archive", msg_id="g:m4", thread=True)

        mock_adapter.read_message.assert_not_awaited()
        mock_adapter.modify_thread.assert_awaited_once_with("g:t1", "archive", None)

    @pytest.mark.asyncio
    async def test_thread_ref_passes_through(self, gmail_source):
        """A ref from a --threads listing is already a thread ID."""
        mock_adapter = AsyncMock()
        mock_adapter.modify_thread.return_value = {"id": "g:t1", "status": "archived"}
        mock_adapter.read_message.side_effect = Exception("not a message")

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter), \
                patch("ts4k.commands.cache.get_message", return_value=None):
            await manage_message(action="archive", msg_id="g:t1", thread=True)

        mock_adapter.modify_thread.assert_awaited_once_with("g:t1", "archive", None)

    @pytest.mark.asyncio
    async def test_label_thread(self, gmail_source):
        mock_adapter = AsyncMock()
        mock_adapter.modify_thread.return_value = {
            "id": "g:t1", "status": "labeled", "label": "triage",
        }

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter), \
                patch("ts4k.commands.cache.get_message",
                      return_value={"id": "g:m4", "thread_id": "g:t1"}):
            result = await manage_message(
                action="label", msg_id="g:m4", label="triage", thread=True,
            )

        mock_adapter.modify_thread.assert_awaited_once_with("g:t1", "label", "triage")
        assert "labeled" in result

    @pytest.mark.asyncio
    async def test_label_without_name_errors(self, gmail_source):
        mock_adapter = AsyncMock()
        with patch("ts4k.commands._make_adapter", return_value=mock_adapter):
            result = await manage_message(action="label", msg_id="g:m4", thread=True)

        assert "--label required" in result
        mock_adapter.modify_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_is_one_call_per_thread(self, gmail_source):
        mock_adapter = AsyncMock()
        mock_adapter.modify_thread.return_value = {"id": "g:t", "status": "archived"}

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter), \
                patch("ts4k.commands.cache.get_message",
                      side_effect=lambda mid, mailbox=None: {"id": mid, "thread_id": f"g:t{mid[-1]}"}):
            result = await manage_message(
                action="archive", msg_id="g:m1,g:m2", thread=True,
            )

        assert mock_adapter.modify_thread.await_count == 2
        assert result.count("archived") == 2

    @pytest.mark.asyncio
    async def test_two_refs_in_same_thread_dedupe_to_one_call(self, gmail_source):
        """Two message refs that resolve to the same thread must issue only
        one modify_thread call — and the second item is reported as covered,
        not as an error."""
        mock_adapter = AsyncMock()
        mock_adapter.modify_thread.return_value = {"id": "g:t1", "status": "trashed"}

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter), \
                patch("ts4k.commands.cache.get_message",
                      return_value={"thread_id": "g:t1"}):
            result = await manage_message(
                action="trash", msg_id="g:m4,g:m3", thread=True,
            )

        mock_adapter.modify_thread.assert_awaited_once_with("g:t1", "trash", None)

        lines = result.split("\n")
        assert len(lines) == 2
        assert "g:m4: trashed" in lines[0]
        assert "error" not in lines[1].lower()
        assert "g:m3" in lines[1] and "g:m4" in lines[1]  # covered by the first item

    @pytest.mark.asyncio
    async def test_dry_run_marks_thread_scope_and_acts_on_nothing(self, gmail_source):
        mock_adapter = AsyncMock()
        with patch("ts4k.commands._make_adapter", return_value=mock_adapter):
            result = await manage_message(
                action="archive", msg_id="g:m4", thread=True, dry_run=True,
            )

        assert "would archive (whole thread)" in result
        mock_adapter.modify_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_gmail_source_gets_clear_error(self, monkeypatch, tmp_path):
        """O365 has no thread equivalent — say so, don't half-do it."""
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        from ts4k.state import sources

        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
        sources.add("o", provider="o365", client_id="cid", level="modify")

        from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig

        adapter = O365Adapter(O365AdapterConfig(client_id="cid", level="modify"), prefix="o")
        adapter.connect = AsyncMock()
        adapter.disconnect = AsyncMock()
        adapter.read_message = AsyncMock(return_value={"id": "o:abc", "thread_id": "o:conv"})

        with patch("ts4k.commands._make_adapter", return_value=adapter), \
                patch("ts4k.commands.cache.get_message", return_value=None):
            result = await manage_message(action="archive", msg_id="o:abc", thread=True)

        assert "thread operations not supported" in result
        assert "'o'" in result

    @pytest.mark.asyncio
    async def test_message_level_path_is_unchanged(self, gmail_source):
        """Default (no --thread) still calls the per-message method."""
        mock_adapter = AsyncMock()
        mock_adapter.archive_message.return_value = {"id": "g:m4", "status": "archived"}

        with patch("ts4k.commands._make_adapter", return_value=mock_adapter):
            result = await manage_message(action="archive", msg_id="g:m4")

        mock_adapter.archive_message.assert_awaited_once_with("g:m4")
        mock_adapter.modify_thread.assert_not_awaited()
        assert "archived" in result


# ---------------------------------------------------------------------------
# collapse_threads
# ---------------------------------------------------------------------------


class TestCollapseThreads:
    def test_one_row_per_thread(self):
        rows = collapse_threads(THREAD_MESSAGES)
        assert len(rows) == 2
        assert [r["id"] for r in rows] == ["g:t1", "g:t2"]

    def test_row_carries_count_participants_and_range(self):
        row = collapse_threads(THREAD_MESSAGES)[0]
        assert row["message_count"] == 4
        # Deduplicated (Alice sent twice), newest sender first.
        assert row["participants"] == [
            "alice@acme.com",
            "carol@acme.com",
            "bob@corp.com",
        ]
        assert row["first_date"] == "2026-02-18T08:00:00Z"
        assert row["date"] == "2026-02-20T16:00:00Z"

    def test_subject_from_oldest_snippet_from_newest(self):
        row = collapse_threads(THREAD_MESSAGES)[0]
        assert row["subject"] == "Q3 planning"  # not "Re: Q3 planning"
        assert row["snippet"] == "Agreed, let's lock the room for Thursday."

    def test_unread_if_any_message_unread(self):
        rows = collapse_threads(THREAD_MESSAGES)
        assert rows[0]["unread"] is True
        assert rows[1]["unread"] is False

    def test_row_id_is_thread_id(self):
        row = collapse_threads(THREAD_MESSAGES)[0]
        assert row["id"] == row["thread_id"] == "g:t1"

    def test_message_without_thread_id_stands_alone(self):
        rows = collapse_threads([{"id": "w:x", "from": "a", "date": "2026-01-01T00:00:00Z"}])
        assert len(rows) == 1
        assert rows[0]["id"] == "w:x"
        assert rows[0]["message_count"] == 1

    def test_empty_input(self):
        assert collapse_threads([]) == []

    def test_whatsapp_messages_collapse_by_chat_jid(self):
        """No thread_id, but chat_jid present — group by a derived chat key."""
        wa_messages = [
            {
                "id": "w:msg2",
                "source": "w",
                "chat_jid": "123456@g.us",
                "from": "alice",
                "subject": "Team chat",
                "date": "2026-02-19T10:00:00Z",
                "snippet": "See you then.",
            },
            {
                "id": "w:msg1",
                "source": "w",
                "chat_jid": "123456@g.us",
                "from": "bob",
                "subject": "Team chat",
                "date": "2026-02-18T09:00:00Z",
                "snippet": "Kickoff.",
            },
        ]
        rows = collapse_threads(wa_messages)

        assert len(rows) == 1
        assert rows[0]["id"] == rows[0]["thread_id"] == "w:123456@g.us"
        assert rows[0]["message_count"] == 2

    def test_whatsapp_snippet_falls_back_to_body(self):
        """WhatsApp rows carry content in body, not snippet — the latest
        message's text must still surface on the collapsed row."""
        wa_messages = [
            {
                "id": "w:msg2", "source": "w", "chat_jid": "123456@g.us",
                "from": "alice", "date": "2026-02-19T10:00:00Z",
                "body": "See you then.",
            },
            {
                "id": "w:msg1", "source": "w", "chat_jid": "123456@g.us",
                "from": "bob", "date": "2026-02-18T09:00:00Z",
                "body": "Kickoff.",
            },
        ]
        rows = collapse_threads(wa_messages)
        assert len(rows) == 1
        assert rows[0]["snippet"] == "See you then."

    def test_whatsapp_thread_ref_resolves_via_read_thread(self):
        """The derived row id, once stripped of its source prefix, is a chat
        JID — exactly what WhatsAppAdapter.read_thread treats as a chat
        lookup (see the ``"@" in raw_id`` branch)."""
        from ts4k.adapters.whatsapp import WhatsAppAdapter, WhatsAppAdapterConfig

        msg = {
            "id": "w:msg1", "source": "w", "chat_jid": "555@s.whatsapp.net",
            "from": "carol", "date": "2026-02-18T09:00:00Z",
        }
        row = collapse_threads([msg])[0]

        adapter = WhatsAppAdapter(WhatsAppAdapterConfig(), prefix="w")
        raw_id = adapter._strip_prefix(row["id"])
        assert raw_id == "555@s.whatsapp.net"
        assert "@" in raw_id  # read_thread's chat-JID branch


# ---------------------------------------------------------------------------
# format_thread_listing
# ---------------------------------------------------------------------------


class TestThreadListingFormat:
    def test_pipe_row_content(self):
        rows = collapse_threads(THREAD_MESSAGES)
        out = format_thread_listing(rows, fmt="pipe", ref_map={"g:t1": 1, "g:t2": 2})
        lines = out.split("\n")

        assert lines[0] == " |N|SOURCE|WHO|SUBJECT|DATES|MSGS|SNIPPET"
        assert len(lines) == 3

        fields = lines[1].split("|")
        assert fields[0] == "*"  # unread marker
        assert fields[1] == "1"  # ref
        assert fields[2] == "g"
        assert fields[3] == "alice@acme.com,carol@acme.com,bob@corp.com"
        assert fields[4] == "Q3 planning"
        assert fields[5] == "18Feb-20Feb"  # date range
        assert fields[6] == "4"  # message count

    def test_single_day_thread_shows_one_timestamp(self):
        rows = collapse_threads([
            {"id": "g:a", "thread_id": "g:t", "from": "a@x", "subject": "S",
             "date": "2026-02-18T08:00:00Z"},
            {"id": "g:b", "thread_id": "g:t", "from": "b@x", "subject": "Re: S",
             "date": "2026-02-18T09:30:00Z"},
        ])
        out = format_thread_listing(rows, fmt="pipe", ref_map={"g:t": 1})
        assert "|09:30|2" in out
        assert "-" not in out.split("\n")[1]

    def test_participants_truncate_with_plus_n(self):
        msgs = [
            {"id": f"g:m{i}", "thread_id": "g:t", "from": f"p{i}@x.com",
             "subject": "S", "date": f"2026-02-1{i}T08:00:00Z"}
            for i in range(5)
        ]
        out = format_thread_listing(collapse_threads(msgs), fmt="pipe", ref_map={"g:t": 1})
        assert "p0@x.com,p1@x.com,p2@x.com+2" in out

    def test_full_ids_without_ref_map(self):
        rows = collapse_threads(THREAD_MESSAGES)
        out = format_thread_listing(rows, fmt="pipe")
        assert out.split("\n")[0].startswith(" |ID|SOURCE|")
        assert "|g:t1|" in out

    def test_json_format(self):
        import json

        rows = collapse_threads(THREAD_MESSAGES)
        data = json.loads(format_thread_listing(rows, fmt="json"))
        assert len(data) == 2
        assert data[0]["id"] == "g:t1"
        assert data[0]["message_count"] == 4
        assert len(data[0]["participants"]) == 3
        assert data[0]["first_date"] == "2026-02-18T08:00:00Z"

    def test_xml_format(self):
        from xml.etree import ElementTree as ET

        rows = collapse_threads(THREAD_MESSAGES)
        root = ET.fromstring(format_thread_listing(rows, fmt="xml"))
        assert root.tag == "threads"
        assert len(root) == 2
        assert root[0].get("id") == "g:t1"
        assert root[0].get("n") == "4"

    def test_xml_carries_the_latest_snippet(self):
        """XML clients get the same latest-message text pipe and JSON show."""
        from xml.etree import ElementTree as ET

        msgs = [
            {"id": "g:m1", "thread_id": "g:t", "from": "a@x.com", "subject": "S",
             "date": "2026-02-18T08:00:00Z", "snippet": "first"},
            {"id": "g:m2", "thread_id": "g:t", "from": "b@x.com", "subject": "S",
             "date": "2026-02-19T08:00:00Z", "snippet": "latest word"},
        ]
        root = ET.fromstring(format_thread_listing(collapse_threads(msgs), fmt="xml"))
        assert root[0].get("snip") == "latest word"

    def test_xml_omits_snip_when_there_is_no_snippet(self):
        """An empty attribute on every row is pure token cost."""
        from xml.etree import ElementTree as ET

        msgs = [
            {"id": "g:m1", "thread_id": "g:t", "from": "a@x.com", "subject": "S",
             "date": "2026-02-18T08:00:00Z"},
        ]
        out = format_thread_listing(collapse_threads(msgs), fmt="xml")
        assert ET.fromstring(out)[0].get("snip") is None
        assert "snip=" not in out

    def test_unknown_format_raises(self):
        with pytest.raises(ValueError):
            format_thread_listing([], fmt="yaml")


def _both_formats(messages: list[dict]) -> tuple[str, str]:
    """Render *messages* per-message and thread-collapsed, both with refs."""
    per_message = format_listing(
        messages, fmt="pipe",
        ref_map={m["id"]: i + 1 for i, m in enumerate(messages)},
    )
    rows = collapse_threads(messages)
    collapsed = format_thread_listing(
        rows, fmt="pipe", ref_map={r["id"]: i + 1 for i, r in enumerate(rows)},
    )
    return per_message, collapsed


class TestThreadListingTokenSavings:
    def test_long_thread_collapses_to_a_fraction(self):
        """AC: collapsed output is significantly smaller for multi-message threads."""
        messages = [
            {
                "id": f"g:m{i}",
                "thread_id": "g:t1",
                "from": f"person{i}@acme.com",
                "subject": "Re: Q3 planning offsite agenda",
                "date": f"2026-02-{10 + i:02d}T09:00:00Z",
                "snippet": f"Reply number {i} with some discussion of the agenda.",
                "source": "g",
            }
            for i in range(10)
        ]
        per_message, collapsed = _both_formats(messages)

        # 10 rows -> 1 row; the participant list is capped, so the win scales.
        assert len(collapsed) < len(per_message) * 0.25, (
            f"collapsed {len(collapsed)}b vs per-message {len(per_message)}b"
        )
        assert len(collapsed.split("\n")) == 2  # header + one thread row

    def test_mixed_listing_still_shrinks(self):
        per_message, collapsed = _both_formats(THREAD_MESSAGES)

        assert len(collapsed) < len(per_message)
        assert len(collapsed.split("\n")) < len(per_message.split("\n"))


# ---------------------------------------------------------------------------
# Listing integration — refs resolve to threads
# ---------------------------------------------------------------------------


class TestThreadListingIntegration:
    @pytest.mark.asyncio
    async def test_list_threads_assigns_refs_to_threads(self, gmail_source):
        from ts4k import commands

        mock_adapter = AsyncMock()
        mock_adapter.list_messages.return_value = THREAD_MESSAGES

        refs = RefTable()
        with patch("ts4k.commands._make_adapter", return_value=mock_adapter):
            result = await commands.list_messages(
                source="g", count=20, ref_table=refs, threads=True,
            )

        assert result.ref_map == {"g:t1": 1, "g:t2": 2}
        assert refs.resolve("1") == "g:t1"
        # 5 messages processed, collapsed to a header plus 2 thread rows.
        assert result.messages_processed == 5
        assert len(result.output.split("\n")) == 3
        assert "|4|" in result.output  # the 4-message thread row

    @pytest.mark.asyncio
    async def test_message_mode_remains_the_default(self, gmail_source):
        from ts4k import commands

        mock_adapter = AsyncMock()
        mock_adapter.list_messages.return_value = THREAD_MESSAGES

        refs = RefTable()
        with patch("ts4k.commands._make_adapter", return_value=mock_adapter):
            result = await commands.list_messages(source="g", count=20, ref_table=refs)

        assert result.messages_processed == 5
        assert refs.resolve("1") == "g:m4"


# ---------------------------------------------------------------------------
# Continuation hint — must retain --threads across pagination
# ---------------------------------------------------------------------------


def _wa_style_messages(n: int, base_hour: int = 1) -> list[dict]:
    return [
        {
            "id": f"g:m{i}",
            "thread_id": f"g:t{i}",
            "source": "g",
            "from": f"s{i}@test.com",
            "subject": f"Subj {i}",
            "date": f"2026-03-08T{base_hour + i:02d}:00:00Z",
        }
        for i in range(n)
    ]


class TestContinuationHintThreads:
    @pytest.mark.asyncio
    async def test_continuation_hint_retains_threads_flag(self, gmail_source, monkeypatch):
        """An overflowing --threads listing must not silently drop --threads
        from the printed continuation command — copying it should keep
        paginating in thread mode, not switch back to message rows."""
        from ts4k import commands

        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _wa_style_messages(10)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands.updates(since="1d", source="g", count=3, threads=True)

        assert result.has_more is True
        assert "--threads" in result._continuation_hint

    @pytest.mark.asyncio
    async def test_continuation_hint_omits_threads_in_message_mode(self, gmail_source, monkeypatch):
        """Message-mode listings must not gain a spurious --threads flag."""
        from ts4k import commands

        async def fake_fetch(prefix, cfg, since, count, **kwargs):
            return _wa_style_messages(10)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands.updates(since="1d", source="g", count=3)

        assert result.has_more is True
        assert "--threads" not in result._continuation_hint

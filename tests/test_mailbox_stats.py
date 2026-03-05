"""Tests for mailbox stats — live label/folder counts (Issue #9).

Tests cover:
- Gmail mailbox_stats() with mocked batch response
- Gmail partial failures (some labels error)
- O365 mailbox_stats() with mocked httpx
- O365 Focused Inbox available/unavailable
- format_mailbox_stats() — all 3 formats
- get_status() with/without mailbox_stats (backward compat)
- get_mailbox_stats() with adapter failure
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig
from ts4k.adapters.o365 import O365Adapter, O365AdapterConfig
from ts4k.core.format import format_mailbox_stats


# ---------------------------------------------------------------------------
# Gmail mailbox_stats
# ---------------------------------------------------------------------------


def _make_label_response(label_id: str, total: int, unread: int) -> dict:
    return {
        "id": label_id,
        "name": label_id,
        "messagesTotal": total,
        "messagesUnread": unread,
    }


@pytest.fixture
def gmail_adapter():
    adapter = GmailAdapter(
        GmailAdapterConfig(user_email="test@gmail.com"),
        prefix="g",
    )
    # Mock the service
    service = MagicMock()
    adapter._service = service
    return adapter, service


@pytest.mark.asyncio
async def test_gmail_mailbox_stats(gmail_adapter):
    adapter, service = gmail_adapter

    label_responses = {
        "INBOX": _make_label_response("INBOX", 142, 23),
        "CATEGORY_PERSONAL": _make_label_response("CATEGORY_PERSONAL", 89, 12),
        "CATEGORY_SOCIAL": _make_label_response("CATEGORY_SOCIAL", 31, 5),
        "CATEGORY_PROMOTIONS": _make_label_response("CATEGORY_PROMOTIONS", 18, 4),
        "CATEGORY_UPDATES": _make_label_response("CATEGORY_UPDATES", 4, 2),
        "CATEGORY_FORUMS": _make_label_response("CATEGORY_FORUMS", 0, 0),
        "SPAM": _make_label_response("SPAM", 47, 0),
        "TRASH": _make_label_response("TRASH", 12, 0),
    }

    batch = MagicMock()

    def mock_execute():
        # Simulate batch callback for each added request
        for call in batch.add.call_args_list:
            request_id = call[1].get("request_id") or call[0][0]
            if isinstance(request_id, str) and request_id in label_responses:
                batch_cb = service.new_batch_http_request.call_args[1]["callback"]
                batch_cb(request_id, label_responses[request_id], None)

    batch.execute = mock_execute
    service.new_batch_http_request.return_value = batch
    service.users.return_value.labels.return_value.get.return_value = MagicMock()

    with patch("ts4k.adapters.gmail.asyncio.to_thread", new_callable=lambda: lambda f: _run_sync(f)):
        result = await adapter.mailbox_stats()

    assert result is not None
    assert result["provider"] == "gmail"
    assert len(result["labels"]) == 8
    assert result["labels"][0] == {"name": "Inbox", "total": 142, "unread": 23}
    assert result["labels"][6] == {"name": "Spam", "total": 47, "unread": 0}


@pytest.mark.asyncio
async def test_gmail_mailbox_stats_partial_failure(gmail_adapter):
    adapter, service = gmail_adapter

    batch = MagicMock()

    def mock_execute():
        batch_cb = service.new_batch_http_request.call_args[1]["callback"]
        # Only INBOX succeeds, SPAM errors
        batch_cb("INBOX", _make_label_response("INBOX", 100, 10), None)
        batch_cb("SPAM", None, Exception("API error"))

    batch.execute = mock_execute
    service.new_batch_http_request.return_value = batch
    service.users.return_value.labels.return_value.get.return_value = MagicMock()

    with patch("ts4k.adapters.gmail.asyncio.to_thread", new_callable=lambda: lambda f: _run_sync(f)):
        result = await adapter.mailbox_stats()

    assert result is not None
    # Should have at least Inbox, missing ones that errored
    labels_by_name = {l["name"]: l for l in result["labels"]}
    assert "Inbox" in labels_by_name
    assert labels_by_name["Inbox"]["total"] == 100


async def _run_sync(f):
    """Helper to run sync functions directly (replaces asyncio.to_thread in tests)."""
    return f()


# ---------------------------------------------------------------------------
# O365 mailbox_stats
# ---------------------------------------------------------------------------


@pytest.fixture
def o365_adapter():
    adapter = O365Adapter(
        O365AdapterConfig(client_id="test-id", tenant_id="common"),
        prefix="o",
    )
    client = AsyncMock()
    adapter._client = client
    return adapter, client


def _make_folder_response(folders):
    return {"value": [
        {"displayName": name, "totalItemCount": total, "unreadItemCount": unread}
        for name, total, unread in folders
    ]}


@pytest.mark.asyncio
async def test_o365_mailbox_stats(o365_adapter):
    adapter, client = o365_adapter

    main_response = _make_folder_response([
        ("Inbox", 89, 15),
        ("Sent Items", 200, 0),
        ("Drafts", 5, 5),
        ("Junk Email", 30, 0),
        ("Deleted Items", 10, 0),
        ("Archive", 500, 0),  # not in target set
    ])

    child_response = _make_folder_response([
        ("Focused", 60, 10),
        ("Other", 29, 5),
    ])

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    call_count = 0

    async def mock_get(path, params=None):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "childFolders" in path:
            resp.json.return_value = child_response
        else:
            resp.json.return_value = main_response
        return resp

    client.get = mock_get

    result = await adapter.mailbox_stats()

    assert result is not None
    assert result["provider"] == "o365"
    labels_by_name = {l["name"]: l for l in result["labels"]}
    assert "Inbox" in labels_by_name
    assert labels_by_name["Inbox"]["total"] == 89
    assert "Sent Items" in labels_by_name
    assert "Archive" not in labels_by_name  # filtered out
    assert "Focused" in labels_by_name
    assert "Other" in labels_by_name


@pytest.mark.asyncio
async def test_o365_mailbox_stats_no_focused(o365_adapter):
    adapter, client = o365_adapter

    main_response = _make_folder_response([
        ("Inbox", 50, 5),
        ("Junk Email", 10, 0),
    ])

    async def mock_get(path, params=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "childFolders" in path:
            raise Exception("Not available")
        resp.json.return_value = main_response
        return resp

    client.get = mock_get

    result = await adapter.mailbox_stats()

    assert result is not None
    labels_by_name = {l["name"]: l for l in result["labels"]}
    assert "Inbox" in labels_by_name
    assert "Focused" not in labels_by_name  # gracefully absent


# ---------------------------------------------------------------------------
# format_mailbox_stats — all 3 formats
# ---------------------------------------------------------------------------


SAMPLE_STATS = {
    "g": {
        "provider": "gmail",
        "labels": [
            {"name": "Inbox", "total": 142, "unread": 23},
            {"name": "Spam", "total": 47, "unread": 0},
        ],
    },
}


def test_format_mailbox_stats_pipe():
    out = format_mailbox_stats(SAMPLE_STATS, fmt="pipe")
    assert "Mailbox (g, gmail):" in out
    assert "LABEL|TOTAL|UNREAD" in out
    assert "Inbox|142|23" in out
    assert "Spam|47|0" in out


def test_format_mailbox_stats_pipe_offline():
    out = format_mailbox_stats({"g": None}, fmt="pipe")
    assert "Mailbox (g): (offline)" in out


def test_format_mailbox_stats_json():
    out = format_mailbox_stats(SAMPLE_STATS, fmt="json")
    data = json.loads(out)
    assert "mailbox" in data
    assert len(data["mailbox"]) == 1
    assert data["mailbox"][0]["source"] == "g"
    assert data["mailbox"][0]["provider"] == "gmail"
    assert len(data["mailbox"][0]["labels"]) == 2


def test_format_mailbox_stats_json_offline():
    out = format_mailbox_stats({"g": None}, fmt="json")
    data = json.loads(out)
    assert data["mailbox"][0]["error"] == "offline"


def test_format_mailbox_stats_xml():
    out = format_mailbox_stats(SAMPLE_STATS, fmt="xml")
    assert "<mailbox>" in out
    assert 'prefix="g"' in out
    assert 'provider="gmail"' in out
    assert 'name="Inbox"' in out
    assert 'total="142"' in out
    assert "</mailbox>" in out


def test_format_mailbox_stats_xml_offline():
    out = format_mailbox_stats({"g": None}, fmt="xml")
    assert 'error="offline"' in out


# ---------------------------------------------------------------------------
# get_status backward compat
# ---------------------------------------------------------------------------


def test_get_status_without_mailbox_stats(tmp_path, monkeypatch):
    """get_status() without mailbox_stats should work as before."""
    from ts4k import commands, state

    state.set_config_dir(tmp_path, reason="test")
    monkeypatch.setattr("ts4k.state.sources.list_all", lambda: {})
    monkeypatch.setattr("ts4k.state.contacts.list_all", lambda: {})
    monkeypatch.setattr("ts4k.state.filters.get_config", lambda: {})
    monkeypatch.setattr("ts4k.state.watermarks.all", lambda: {})
    monkeypatch.setattr("ts4k.state.stats.get_all", lambda: {})
    monkeypatch.setattr("ts4k.state.stats.savings_pct", lambda: 0)
    monkeypatch.setattr("ts4k.state.cache.stats", lambda: {
        "total": 0, "bodies": 0, "by_source": {}, "index_bytes": 0, "bodies_bytes": 0,
    })

    out = commands.get_status()
    assert "Sources:" in out
    assert "Mailbox" not in out  # no mailbox section without --live


def test_get_status_with_mailbox_stats(tmp_path, monkeypatch):
    """get_status() with mailbox_stats appends Mailbox section."""
    from ts4k import commands, state

    state.set_config_dir(tmp_path, reason="test")
    monkeypatch.setattr("ts4k.state.sources.list_all", lambda: {
        "g": {"provider": "gmail", "email": "test@gmail.com"},
    })
    monkeypatch.setattr("ts4k.state.contacts.list_all", lambda: {})
    monkeypatch.setattr("ts4k.state.filters.get_config", lambda: {})
    monkeypatch.setattr("ts4k.state.watermarks.all", lambda: {})
    monkeypatch.setattr("ts4k.state.stats.get_all", lambda: {})
    monkeypatch.setattr("ts4k.state.stats.savings_pct", lambda: 0)
    monkeypatch.setattr("ts4k.state.cache.stats", lambda: {
        "total": 0, "bodies": 0, "by_source": {}, "index_bytes": 0, "bodies_bytes": 0,
    })

    mbox = {"g": {"provider": "gmail", "labels": [
        {"name": "Inbox", "total": 42, "unread": 5},
    ]}}
    out = commands.get_status(mailbox_stats_data=mbox)
    assert "Mailbox (g, gmail):" in out
    assert "Inbox|42|5" in out


# ---------------------------------------------------------------------------
# get_mailbox_stats with adapter failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mailbox_stats_adapter_failure(tmp_path, monkeypatch):
    """Adapter failure returns None for that prefix, no crash."""
    from ts4k import commands, state

    state.set_config_dir(tmp_path, reason="test")
    monkeypatch.setattr("ts4k.state.sources.list_all", lambda: {
        "g": {"provider": "gmail", "email": "test@gmail.com"},
    })

    # Make connect() raise
    async def mock_connect(self):
        raise RuntimeError("Auth failed")

    monkeypatch.setattr(GmailAdapter, "connect", mock_connect)

    result = await commands.get_mailbox_stats()
    assert "g" in result
    assert result["g"] is None  # failed gracefully

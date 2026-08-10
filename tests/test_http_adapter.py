"""Tests for the generic HTTP notification source adapter (#31).

Unit tests mock httpx.AsyncClient — no live network calls. The response
schema mocked here matches the documented (unshipped) Humans API format
from nobodies-collective/Humans#469; the acceptance criterion "works
with the Humans API format" can't be verified against a live endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ts4k.adapters.http import (
    HTTPAdapter,
    HTTPAdapterConfig,
    _notification_to_header,
    _parse_headers,
    _synthesize_id,
)
from ts4k.state import meters as meters_mod

NOTIFICATIONS_RESPONSE = {
    "notifications": [
        {
            "date": "2026-04-10T09:00:00Z",
            "source": "ConsentReviewNeeded",
            "subject": "Consent review pending",
            "link": "/OnboardingReview",
            "priority": "high",
            "unread": True,
        },
        {
            "id": "sync-failure-42",
            "date": "2026-04-09T12:00:00Z",
            "source": "SyncFailure",
            "subject": "Directory sync failed",
            "link": "/Admin/Sync",
            "unread": False,
        },
    ],
    "meters": [
        {"label": "Board votes needed", "count": 5, "link": "/OnboardingReview/BoardVoting"}
    ],
}


def _mock_response(data, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response with JSON data."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
    return resp


def _make_adapter(prefix: str = "h", header=None) -> HTTPAdapter:
    """Create an HTTPAdapter with a mocked httpx client."""
    config = HTTPAdapterConfig(url="https://example.com/api/notifications", header=header)
    adapter = HTTPAdapter(config, prefix=prefix)
    adapter._client = AsyncMock(spec=httpx.AsyncClient)
    return adapter


@pytest.fixture(autouse=True)
def _isolate_meters(tmp_path, monkeypatch):
    """Meter snapshots persist to disk — isolate them per test."""
    monkeypatch.setattr(meters_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(meters_mod, "_METERS_FILE", tmp_path / "meters.json")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestParseHeaders:
    def test_single_string(self):
        assert _parse_headers("X-Api-Key: abc123") == {"X-Api-Key": "abc123"}

    def test_none(self):
        assert _parse_headers(None) == {}

    def test_comma_separated_single_value(self):
        raw = "X-Api-Key: abc123,Authorization: Bearer xyz"
        assert _parse_headers(raw) == {
            "X-Api-Key": "abc123",
            "Authorization": "Bearer xyz",
        }

    def test_repeated_flag_list(self):
        raw = ["X-Api-Key: abc123", "Authorization: Bearer xyz"]
        assert _parse_headers(raw) == {
            "X-Api-Key": "abc123",
            "Authorization": "Bearer xyz",
        }

    def test_ignores_malformed_entries(self):
        assert _parse_headers("not-a-header, X-Api-Key: abc123") == {
            "X-Api-Key": "abc123",
        }


class TestSynthesizeId:
    def test_deterministic_for_same_source_and_date(self):
        n1 = {"source": "ConsentReviewNeeded", "date": "2026-04-10T09:00:00Z"}
        n2 = {"source": "ConsentReviewNeeded", "date": "2026-04-10T09:00:00Z"}
        assert _synthesize_id(n1) == _synthesize_id(n2)

    def test_differs_for_different_date(self):
        n1 = {"source": "ConsentReviewNeeded", "date": "2026-04-10T09:00:00Z"}
        n2 = {"source": "ConsentReviewNeeded", "date": "2026-04-11T09:00:00Z"}
        assert _synthesize_id(n1) != _synthesize_id(n2)

    def test_differs_for_different_subject(self):
        """#31 follow-up F1 — same source+date but different subject must
        not collide, or the two notifications share one short ref and
        read_message() can only ever return the first."""
        n1 = {"source": "ConsentReviewNeeded", "date": "2026-04-10T09:00:00Z", "subject": "A"}
        n2 = {"source": "ConsentReviewNeeded", "date": "2026-04-10T09:00:00Z", "subject": "B"}
        assert _synthesize_id(n1) != _synthesize_id(n2)

    def test_differs_for_different_link(self):
        n1 = {
            "source": "ConsentReviewNeeded", "date": "2026-04-10T09:00:00Z",
            "subject": "A", "link": "/one",
        }
        n2 = {
            "source": "ConsentReviewNeeded", "date": "2026-04-10T09:00:00Z",
            "subject": "A", "link": "/two",
        }
        assert _synthesize_id(n1) != _synthesize_id(n2)

    def test_stable_across_repeated_polls(self):
        """Same notification re-fetched on a later poll must keep the same
        synthesized ID — it's the whole point of a synthesized identity."""
        notif = {
            "source": "ConsentReviewNeeded", "date": "2026-04-10T09:00:00Z",
            "subject": "Consent review pending", "link": "/OnboardingReview",
        }
        polled_again = dict(notif)
        assert _synthesize_id(notif) == _synthesize_id(polled_again)


class TestNotificationToHeader:
    def test_maps_fields(self):
        notif = NOTIFICATIONS_RESPONSE["notifications"][0]
        header = _notification_to_header(notif, "h")

        assert header["date"] == "2026-04-10T09:00:00Z"
        assert header["subject"] == "ConsentReviewNeeded: Consent review pending"
        assert header["from"] == "ConsentReviewNeeded"
        assert header["link"] == "/OnboardingReview"
        assert header["unread"] is True
        # No "id" in the payload — synthesized from source+date.
        assert header["raw_id"] == _synthesize_id(notif)
        assert header["id"] == f"h:{_synthesize_id(notif)}"
        assert header["thread_id"] == header["id"]
        assert header["source"] == "h"

    def test_uses_provided_id(self):
        notif = NOTIFICATIONS_RESPONSE["notifications"][1]
        header = _notification_to_header(notif, "h")
        assert header["raw_id"] == "sync-failure-42"
        assert header["id"] == "h:sync-failure-42"

    def test_normalizes_non_utc_offset_to_utc(self):
        """#31 review F4 — a valid non-"Z" offset must come out as UTC so
        later lexical comparisons (since-filter, sort) are correct."""
        notif = {"id": "x", "date": "2026-04-09T20:30:00-04:00", "source": "Ops", "subject": "x"}
        header = _notification_to_header(notif, "h")
        assert header["date"] == "2026-04-10T00:30:00Z"

    def test_unparseable_date_degrades_gracefully(self):
        """An unparseable date must pass through unchanged, not vanish."""
        notif = {"id": "x", "date": "not-a-date", "source": "Ops", "subject": "x"}
        header = _notification_to_header(notif, "h")
        assert header["date"] == "not-a-date"


# ---------------------------------------------------------------------------
# whatsnew / list_messages
# ---------------------------------------------------------------------------


class TestWhatsnew:
    @pytest.mark.asyncio
    async def test_returns_mapped_notifications(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        results = await adapter.whatsnew()

        assert len(results) == 2
        assert results[0]["subject"] == "ConsentReviewNeeded: Consent review pending"  # newest first
        assert results[1]["id"] == "h:sync-failure-42"

    @pytest.mark.asyncio
    async def test_since_filters_older(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        results = await adapter.whatsnew(since="2026-04-10T00:00:00Z")

        assert len(results) == 1
        assert results[0]["from"] == "ConsentReviewNeeded"

    @pytest.mark.asyncio
    async def test_count_truncates(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        results = await adapter.whatsnew(count=1)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_since_filter_survives_non_utc_offset(self):
        """#31 review F4 — a notification with a non-"Z" offset that is
        chronologically newer than `since` must not be dropped just
        because it lexically sorts smaller as a raw string."""
        payload = {
            "notifications": [
                {
                    "id": "offset-newer",
                    # == 2026-04-10T00:30:00Z — after the watermark below —
                    # but lexically "...-04:00" < "...Z".
                    "date": "2026-04-09T20:30:00-04:00",
                    "source": "Ops",
                    "subject": "Newer, non-UTC offset",
                },
                {
                    "id": "older",
                    "date": "2026-04-09T00:00:00Z",
                    "source": "Ops",
                    "subject": "Older, UTC",
                },
            ],
            "meters": [],
        }
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(payload))
        results = await adapter.whatsnew(since="2026-04-10T00:00:00Z")

        assert [r["id"] for r in results] == ["h:offset-newer"]

    @pytest.mark.asyncio
    async def test_sort_orders_by_true_time_not_lexical_string(self):
        payload = {
            "notifications": [
                {"id": "a", "date": "2026-04-09T20:30:00-04:00", "source": "Ops", "subject": "a"},
                {"id": "b", "date": "2026-04-10T00:00:00Z", "source": "Ops", "subject": "b"},
            ],
            "meters": [],
        }
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(payload))
        results = await adapter.whatsnew()

        # "a" (20:30 -04:00 == 00:30 UTC) is chronologically after "b" (00:00 UTC).
        assert [r["id"] for r in results] == ["h:a", "h:b"]

    @pytest.mark.asyncio
    async def test_sends_auth_header(self):
        adapter = _make_adapter(header="X-Api-Key: abc123")
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        await adapter.whatsnew()

        call_args = adapter._client.get.call_args
        assert call_args[1]["headers"] == {"X-Api-Key": "abc123"}

    @pytest.mark.asyncio
    async def test_captures_meters(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        await adapter.whatsnew()

        assert meters_mod.get_meters("h") == NOTIFICATIONS_RESPONSE["meters"]

    @pytest.mark.asyncio
    async def test_empty_notifications(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response({"notifications": [], "meters": []})
        )
        assert await adapter.whatsnew() == []

    @pytest.mark.asyncio
    async def test_id_less_notifications_with_different_subjects_get_distinct_ids(self):
        """#31 follow-up F1 — without id, two notifications sharing
        source+date must not collapse onto the same synthesized id."""
        payload = {
            "notifications": [
                {"date": "2026-04-10T09:00:00Z", "source": "Ops", "subject": "First issue"},
                {"date": "2026-04-10T09:00:00Z", "source": "Ops", "subject": "Second issue"},
            ],
            "meters": [],
        }
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(payload))
        results = await adapter.whatsnew()

        ids = {r["id"] for r in results}
        assert len(ids) == 2

    @pytest.mark.asyncio
    async def test_id_less_notification_keeps_same_id_across_polls(self):
        """#31 follow-up F1 — a repeat poll of the same unchanged
        notification must synthesize the same id both times."""
        payload = {
            "notifications": [
                {"date": "2026-04-10T09:00:00Z", "source": "Ops", "subject": "Same issue"},
            ],
            "meters": [],
        }
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(payload))
        first = await adapter.whatsnew()
        second = await adapter.whatsnew()

        assert first[0]["id"] == second[0]["id"]


class TestListMessages:
    @pytest.mark.asyncio
    async def test_filters_by_query(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        results = await adapter.list_messages(query="sync")
        assert len(results) == 1
        assert results[0]["id"] == "h:sync-failure-42"


# ---------------------------------------------------------------------------
# read_message / read_thread
# ---------------------------------------------------------------------------


class TestReadMessage:
    @pytest.mark.asyncio
    async def test_finds_matching_notification(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        msg = await adapter.read_message("h:sync-failure-42")
        assert msg["subject"] == "SyncFailure: Directory sync failed"
        assert "/Admin/Sync" in msg["body"]

    @pytest.mark.asyncio
    async def test_raises_when_not_found(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        with pytest.raises(ValueError):
            await adapter.read_message("h:does-not-exist")


class TestReadThread:
    @pytest.mark.asyncio
    async def test_wraps_single_message(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        thread = await adapter.read_thread("h:sync-failure-42")
        assert thread["message_count"] == 1
        assert thread["messages"][0]["id"] == "h:sync-failure-42"


# ---------------------------------------------------------------------------
# Error handling — timeouts, auth failures, non-JSON, isolation
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))
        assert await adapter.whatsnew() == []

    @pytest.mark.asyncio
    async def test_auth_failure_returns_empty(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response({}, status_code=401))
        assert await adapter.whatsnew() == []

    @pytest.mark.asyncio
    async def test_non_json_response_returns_empty(self):
        adapter = _make_adapter()
        resp = _mock_response({})
        resp.json.side_effect = ValueError("not JSON")
        adapter._client.get = AsyncMock(return_value=resp)
        assert await adapter.whatsnew() == []

    @pytest.mark.asyncio
    async def test_connection_error_returns_empty(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        assert await adapter.whatsnew() == []

    @pytest.mark.asyncio
    async def test_unexpected_shape_returns_empty(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(["not", "a", "dict"]))
        assert await adapter.whatsnew() == []

    @pytest.mark.asyncio
    async def test_failure_does_not_touch_meters(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS_RESPONSE))
        await adapter.whatsnew()
        assert meters_mod.get_meters("h")

        adapter._client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))
        await adapter.whatsnew()
        # A failed poll doesn't clear the last-known-good snapshot.
        assert meters_mod.get_meters("h") == NOTIFICATIONS_RESPONSE["meters"]

    @pytest.mark.asyncio
    async def test_dict_shaped_meters_rejected(self):
        """#31 follow-up F2 — a malformed nested ``meters`` shape (a dict
        instead of a list) must not be persisted, or overview() later
        crashes iterating it."""
        payload = {"notifications": [], "meters": {"label": "x"}}
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(payload))
        assert await adapter.whatsnew() == []
        assert meters_mod.get_meters("h") == []

    @pytest.mark.asyncio
    async def test_meters_list_with_non_object_entry_rejected(self):
        payload = {"notifications": [], "meters": ["not-a-dict"]}
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(payload))
        assert await adapter.whatsnew() == []
        assert meters_mod.get_meters("h") == []


class TestSinceNormalization:
    @pytest.mark.asyncio
    async def test_non_utc_since_is_normalized_before_comparing(self):
        """A watermark with an offset must be converted to UTC too —
        otherwise a "Z" date is compared lexically against a "-04:00" one.
        since=2026-04-09T20:00:00-04:00 IS midnight UTC, so a 23:00Z
        notification (an hour earlier) must be excluded."""
        payload = {
            "notifications": [
                {"id": "older", "date": "2026-04-09T23:00:00Z", "source": "Ops", "subject": "x"},
                {"id": "newer", "date": "2026-04-10T01:00:00Z", "source": "Ops", "subject": "y"},
            ],
            "meters": [],
        }
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(payload))
        results = await adapter.whatsnew(since="2026-04-09T20:00:00-04:00")

        assert [r["id"] for r in results] == ["h:newer"]

"""Tests for the GitHub REST API adapter.

Every HTTP call is mocked — nothing in this file ever reaches
api.github.com.  That matters most for ``mark_read``, which mutates the
real notification inbox when it is not mocked.

Converter and classification tests are pure functions.  Adapter tests
mock ``httpx.AsyncClient`` the same way ``test_o365_adapter.py`` does.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ts4k.adapters.github import (
    GitHubAdapter,
    GitHubAdapterConfig,
    classify,
    parse_id,
    resolve_token,
    _issue_ref_from_url,
    _notification_to_dict,
    _search_item_to_dict,
    _strip_prefix,
)

# ---------------------------------------------------------------------------
# Fixtures — realistic GitHub REST responses
# ---------------------------------------------------------------------------


def _notification(
    nid: str = "2984571234",
    reason: str = "review_requested",
    subject_type: str = "PullRequest",
    title: str = "Add GitHub adapter",
    url: str = "https://api.github.com/repos/peterdrier/ts4k/pulls/42",
    repo: str = "peterdrier/ts4k",
    updated_at: str = "2026-08-09T14:30:00Z",
    unread: bool = True,
) -> dict:
    return {
        "id": nid,
        "unread": unread,
        "reason": reason,
        "updated_at": updated_at,
        "subject": {"title": title, "url": url, "type": subject_type},
        "repository": {"full_name": repo, "name": repo.split("/")[-1]},
        "url": f"https://api.github.com/notifications/threads/{nid}",
    }


NOTIFICATIONS = [
    _notification(),
    _notification(
        nid="2984571235",
        reason="ci_activity",
        subject_type="CheckSuite",
        title="ci workflow run failed for main branch",
        url="https://api.github.com/repos/peterdrier/ts4k/actions/runs/9",
        updated_at="2026-08-09T13:00:00Z",
    ),
]

ISSUE = {
    "number": 42,
    "title": "Add GitHub adapter",
    "body": "We should surface notifications in ts4k.",
    "user": {"login": "peterdrier"},
    "created_at": "2026-08-01T09:00:00Z",
    "updated_at": "2026-08-09T14:30:00Z",
    "state": "open",
    "comments": 2,
    "html_url": "https://github.com/peterdrier/ts4k/issues/42",
}

PULL_REQUEST = dict(ISSUE, pull_request={"url": "https://api.github.com/x"})

COMMENTS = [
    {
        "id": 991,
        "user": {"login": "alice"},
        "created_at": "2026-08-02T10:00:00Z",
        "body": "Sounds good.",
        "html_url": "https://github.com/peterdrier/ts4k/issues/42#issuecomment-991",
    },
    {
        "id": 992,
        "user": {"login": "bob"},
        "created_at": "2026-08-03T11:00:00Z",
        "body": "Which endpoint?",
        "html_url": "https://github.com/peterdrier/ts4k/issues/42#issuecomment-992",
    },
]

SEARCH_RESPONSE = {
    "total_count": 2,
    "items": [
        {
            "url": "https://api.github.com/repos/peterdrier/ts4k/issues/42",
            "repository_url": "https://api.github.com/repos/peterdrier/ts4k",
            "number": 42,
            "title": "Add GitHub adapter",
            "body": "We should surface notifications in ts4k.",
            "user": {"login": "peterdrier"},
            "updated_at": "2026-08-09T14:30:00Z",
            "state": "open",
        },
        {
            "url": "https://api.github.com/repos/peterdrier/ts4k/pulls/43",
            "repository_url": "https://api.github.com/repos/peterdrier/ts4k",
            "number": 43,
            "title": "Fix normalize regression",
            "body": "",
            "user": {"login": "alice"},
            "updated_at": "2026-08-08T08:00:00Z",
            "state": "open",
            "pull_request": {"url": "https://api.github.com/x"},
        },
    ],
}


def _mock_response(
    data: object = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    json_error: bool = False,
) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_error:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=resp,
        )
    return resp


def _make_adapter(prefix: str = "gh", level: str | None = None) -> GitHubAdapter:
    """Create a GitHubAdapter whose httpx client is a mock.

    The token is a literal placeholder — no real credential is read here.
    """
    adapter = GitHubAdapter(
        GitHubAdapterConfig(token="test-token-not-real", level=level), prefix=prefix
    )
    adapter._client = AsyncMock(spec=httpx.AsyncClient)
    return adapter


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


class TestResolveToken:
    def test_explicit_token_wins(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "from-env")
        assert resolve_token("from-config") == "from-config"

    def test_token_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        path = tmp_path / "pat"
        path.write_text("from-file\n", encoding="utf-8")
        assert resolve_token(None, str(path)) == "from-file"

    def test_unreadable_file_falls_back_to_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "from-env")
        assert resolve_token(None, str(tmp_path / "missing")) == "from-env"

    def test_gh_token_env(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GH_TOKEN", "from-gh")
        assert resolve_token() == "from-gh"

    def test_nothing_configured(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert resolve_token() == ""


# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------


class TestClassify:
    def test_review_requested(self):
        assert classify(_notification(reason="review_requested")) == "review"

    def test_approval_requested(self):
        assert classify(_notification(reason="approval_requested")) == "review"

    def test_ci_activity(self):
        assert classify(_notification(reason="ci_activity", subject_type="Issue")) == "ci"

    def test_check_suite_subject_is_ci(self):
        assert classify(_notification(reason="subscribed", subject_type="CheckSuite")) == "ci"

    def test_mention(self):
        assert classify(_notification(reason="mention")) == "mention"

    def test_team_mention(self):
        assert classify(_notification(reason="team_mention")) == "mention"

    def test_assignment(self):
        assert classify(_notification(reason="assign")) == "assignment"

    def test_release_subject(self):
        assert classify(_notification(reason="subscribed", subject_type="Release")) == "release"

    def test_security_alert_is_dependabot(self):
        assert classify(_notification(reason="security_alert", subject_type="Issue")) == "dependabot"

    def test_vulnerability_alert_subject_is_dependabot(self):
        n = _notification(reason="subscribed", subject_type="RepositoryVulnerabilityAlert")
        assert classify(n) == "dependabot"

    def test_dependabot_bump_pr_title(self):
        n = _notification(reason="subscribed", title="Bump httpx from 0.27.0 to 0.28.1")
        assert classify(n) == "dependabot"

    def test_bump_title_on_issue_is_not_dependabot(self):
        n = _notification(reason="comment", subject_type="Issue", title="Bump httpx from 1 to 2")
        assert classify(n) == "comment"

    def test_unmapped_reason_passes_through(self):
        assert classify(_notification(reason="state_change")) == "state_change"

    def test_missing_reason_is_other(self):
        assert classify({"subject": {"type": "Issue"}}) == "other"


# ---------------------------------------------------------------------------
# ID parsing
# ---------------------------------------------------------------------------


class TestParseId:
    def test_notification_id(self):
        assert parse_id("2984571234") == ("notification", ("2984571234",))

    def test_issue_id(self):
        assert parse_id("peterdrier/ts4k#42") == ("issue", ("peterdrier", "ts4k", "42"))

    def test_comment_id(self):
        assert parse_id("peterdrier/ts4k/comments/991") == (
            "comment", ("peterdrier", "ts4k", "991")
        )

    def test_review_comment_id(self):
        assert parse_id("peterdrier/ts4k/pull-comments/6001") == (
            "review_comment", ("peterdrier", "ts4k", "6001")
        )

    def test_review_id(self):
        assert parse_id("peterdrier/ts4k/pulls/42/reviews/5001") == (
            "review", ("peterdrier", "ts4k", "42", "5001")
        )

    def test_garbage_raises(self):
        with pytest.raises(ValueError, match="Unrecognised GitHub ID"):
            parse_id("not-an-id")

    def test_strip_prefix(self):
        assert _strip_prefix("gh:peterdrier/ts4k#42", "gh") == "peterdrier/ts4k#42"
        assert _strip_prefix("peterdrier/ts4k#42", "gh") == "peterdrier/ts4k#42"

    def test_issue_ref_from_pull_url(self):
        assert _issue_ref_from_url(
            "https://api.github.com/repos/peterdrier/ts4k/pulls/42"
        ) == "peterdrier/ts4k#42"

    def test_issue_ref_from_non_issue_url_raises(self):
        with pytest.raises(ValueError):
            _issue_ref_from_url("https://api.github.com/repos/peterdrier/ts4k/releases/9")


# ---------------------------------------------------------------------------
# Converters
# ---------------------------------------------------------------------------


class TestNotificationToDict:
    def test_prefixes_id(self):
        d = _notification_to_dict(_notification(), "gh")
        assert d["id"] == "gh:2984571234"
        assert d["raw_id"] == "2984571234"
        assert d["source"] == "gh"

    def test_thread_id_points_at_the_issue(self):
        d = _notification_to_dict(_notification(), "gh")
        assert d["thread_id"] == "gh:peterdrier/ts4k#42"

    def test_from_is_the_repository(self):
        assert _notification_to_dict(_notification(), "gh")["from"] == "peterdrier/ts4k"

    def test_subject_carries_the_category(self):
        d = _notification_to_dict(_notification(), "gh")
        assert d["subject"] == "[review] Add GitHub adapter"
        assert d["category"] == "review"

    def test_date_is_updated_at(self):
        assert _notification_to_dict(_notification(), "gh")["date"] == "2026-08-09T14:30:00Z"

    def test_unread_flag(self):
        assert _notification_to_dict(_notification(unread=False), "gh")["unread"] is False

    def test_non_issue_subject_has_no_thread_id(self):
        n = _notification(
            subject_type="Release",
            url="https://api.github.com/repos/peterdrier/ts4k/releases/9",
        )
        assert _notification_to_dict(n, "gh")["thread_id"] == ""

    def test_custom_prefix(self):
        assert _notification_to_dict(_notification(), "work")["id"] == "work:2984571234"


class TestSearchItemToDict:
    def test_builds_issue_ref(self):
        d = _search_item_to_dict(SEARCH_RESPONSE["items"][0], "gh")
        assert d["id"] == "gh:peterdrier/ts4k#42"
        assert d["from"] == "peterdrier"
        assert d["subject"] == "Add GitHub adapter"

    def test_flags_pull_requests(self):
        d = _search_item_to_dict(SEARCH_RESPONSE["items"][1], "gh")
        assert d["is_pull_request"] is True
        assert d["id"] == "gh:peterdrier/ts4k#43"

    def test_unparseable_item_is_dropped(self):
        assert _search_item_to_dict({"url": "", "repository_url": ""}, "gh") == {}


# ---------------------------------------------------------------------------
# whatsnew
# ---------------------------------------------------------------------------


class TestWhatsnew:
    @pytest.mark.asyncio
    async def test_returns_normalised_notifications(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS))
        results = await adapter.whatsnew(since="2026-08-01T00:00:00Z")
        assert [r["id"] for r in results] == ["gh:2984571234", "gh:2984571235"]
        assert [r["category"] for r in results] == ["review", "ci"]

    @pytest.mark.asyncio
    async def test_passes_since(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response([]))
        await adapter.whatsnew(since="2026-08-01T00:00:00Z")
        path, kwargs = adapter._client.get.call_args[0][0], adapter._client.get.call_args[1]
        assert path == "/notifications"
        assert kwargs["params"]["since"] == "2026-08-01T00:00:00Z"
        assert kwargs["params"]["all"] == "false"

    @pytest.mark.asyncio
    async def test_default_since_is_last_day(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response([]))
        await adapter.whatsnew()
        assert adapter._client.get.call_args[1]["params"]["since"].endswith("Z")

    @pytest.mark.asyncio
    async def test_sorted_newest_first(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(list(reversed(NOTIFICATIONS)))
        )
        results = await adapter.whatsnew()
        assert results[0]["date"] > results[1]["date"]

    @pytest.mark.asyncio
    async def test_paginates_until_count(self):
        adapter = _make_adapter()
        full_page = [_notification(nid=str(i)) for i in range(50)]
        adapter._client.get = AsyncMock(
            side_effect=[_mock_response(full_page), _mock_response(NOTIFICATIONS)]
        )
        results = await adapter.whatsnew(count=60)
        assert adapter._client.get.call_count == 2
        assert len(results) == 52

    @pytest.mark.asyncio
    async def test_stops_on_short_page(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS))
        await adapter.whatsnew(count=200)
        assert adapter._client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_truncates_to_count(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS))
        assert len(await adapter.whatsnew(count=1)) == 1

    @pytest.mark.asyncio
    async def test_sender_filter_returns_nothing(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS))
        assert await adapter.whatsnew(sender="alice@example.com") == []
        adapter._client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_domain_filter_returns_nothing(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS))
        assert await adapter.whatsnew(domain="example.com") == []
        adapter._client.get.assert_not_called()


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------


class TestListMessages:
    @pytest.mark.asyncio
    async def test_searches_issues(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(SEARCH_RESPONSE))
        results = await adapter.list_messages(query="repo:peterdrier/ts4k is:open")
        assert adapter._client.get.call_args[0][0] == "/search/issues"
        assert adapter._client.get.call_args[1]["params"]["q"] == "repo:peterdrier/ts4k is:open"
        assert [r["id"] for r in results] == ["gh:peterdrier/ts4k#42", "gh:peterdrier/ts4k#43"]

    @pytest.mark.asyncio
    async def test_no_query_falls_back_to_notifications(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS))
        results = await adapter.list_messages()
        assert adapter._client.get.call_args[0][0] == "/notifications"
        assert results[0]["id"] == "gh:2984571234"

    @pytest.mark.asyncio
    async def test_no_query_does_not_apply_whatsnew_one_day_cutoff(self):
        """A notification that's been unread for more than 24 hours must
        still show up in a plain queryless list — only whatsnew() applies
        the 24-hour "since" default."""
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(NOTIFICATIONS))
        await adapter.list_messages()
        params = adapter._client.get.call_args[1]["params"]
        assert "since" not in params
        assert params["all"] == "false"

    @pytest.mark.asyncio
    async def test_page_token_selects_page(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(SEARCH_RESPONSE))
        await adapter.list_messages(query="x", page_token="3")
        assert adapter._client.get.call_args[1]["params"]["page"] == 3

    @pytest.mark.asyncio
    async def test_next_page_token_when_more_results(self):
        adapter = _make_adapter()
        data = dict(SEARCH_RESPONSE, total_count=50)
        adapter._client.get = AsyncMock(return_value=_mock_response(data))
        results = await adapter.list_messages(query="x", count=2)
        assert results[-1]["_next_page_token"] == "2"

    @pytest.mark.asyncio
    async def test_next_page_token_uses_actual_page_size_not_count(self):
        """count=200 exceeds GitHub's 100-item search cap, so per_page is
        capped at 100. If the pagination check compared against count
        instead of the real page size, 200 < 150 would be False and the
        next-page token would be dropped even though 50 matches remain."""
        adapter = _make_adapter()
        data = dict(SEARCH_RESPONSE, total_count=150)
        adapter._client.get = AsyncMock(return_value=_mock_response(data))
        results = await adapter.list_messages(query="x", count=200)
        assert adapter._client.get.call_args[1]["params"]["per_page"] == 100
        assert results[-1]["_next_page_token"] == "2"

    @pytest.mark.asyncio
    async def test_no_next_page_token_on_last_page(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(SEARCH_RESPONSE))
        results = await adapter.list_messages(query="x", count=20)
        assert "_next_page_token" not in results[-1]

    @pytest.mark.asyncio
    async def test_sender_filter_returns_nothing(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(SEARCH_RESPONSE))
        assert await adapter.list_messages(query="x", sender="a@b.com") == []


# ---------------------------------------------------------------------------
# read_message / read_thread
# ---------------------------------------------------------------------------


class TestReadMessage:
    @pytest.mark.asyncio
    async def test_reads_issue_by_ref(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(ISSUE))
        msg = await adapter.read_message("gh:peterdrier/ts4k#42")
        assert adapter._client.get.call_args[0][0] == "/repos/peterdrier/ts4k/issues/42"
        assert msg["id"] == "gh:peterdrier/ts4k#42"
        assert msg["from"] == "peterdrier"
        assert msg["body"] == "We should surface notifications in ts4k."

    @pytest.mark.asyncio
    async def test_flags_pull_requests(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(PULL_REQUEST))
        msg = await adapter.read_message("gh:peterdrier/ts4k#42")
        assert msg["is_pull_request"] is True

    @pytest.mark.asyncio
    async def test_resolves_notification_id_to_its_issue(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[_mock_response(_notification()), _mock_response(ISSUE)]
        )
        msg = await adapter.read_message("gh:2984571234")
        calls = [c[0][0] for c in adapter._client.get.call_args_list]
        assert calls == [
            "/notifications/threads/2984571234",
            "/repos/peterdrier/ts4k/issues/42",
        ]
        assert msg["id"] == "gh:peterdrier/ts4k#42"

    @pytest.mark.asyncio
    async def test_notification_without_issue_subject_raises(self):
        adapter = _make_adapter()
        release = _notification(
            subject_type="Release",
            url="https://api.github.com/repos/peterdrier/ts4k/releases/9",
        )
        adapter._client.get = AsyncMock(return_value=_mock_response(release))
        with pytest.raises(ValueError, match="is a Release"):
            await adapter.read_message("gh:2984571234")

    @pytest.mark.asyncio
    async def test_discussion_notification_reports_the_limit(self):
        adapter = _make_adapter()
        discussion = _notification(
            subject_type="Discussion",
            url="https://github.com/peterdrier/ts4k/discussions/7",
        )
        adapter._client.get = AsyncMock(return_value=_mock_response(discussion))
        with pytest.raises(ValueError, match="is a Discussion"):
            await adapter.read_message("gh:2984571234")

    @pytest.mark.asyncio
    async def test_reads_a_comment(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(COMMENTS[0]))
        msg = await adapter.read_message("gh:peterdrier/ts4k/comments/991")
        assert adapter._client.get.call_args[0][0] == (
            "/repos/peterdrier/ts4k/issues/comments/991"
        )
        assert msg["from"] == "alice"
        assert msg["id"] == "gh:peterdrier/ts4k/comments/991"

    @pytest.mark.asyncio
    async def test_reads_a_pull_request_review_comment(self):
        """Inline review comments are numbered in their own sequence and
        only exist under /pulls/comments — an issue-comment ID of the same
        number is a different object entirely."""
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(REVIEW_COMMENTS[0]))
        msg = await adapter.read_message("gh:peterdrier/ts4k/pull-comments/6001")
        assert adapter._client.get.call_args[0][0] == (
            "/repos/peterdrier/ts4k/pulls/comments/6001"
        )
        assert msg["from"] == "bob"
        assert msg["id"] == "gh:peterdrier/ts4k/pull-comments/6001"

    @pytest.mark.asyncio
    async def test_review_comment_is_not_a_thread(self):
        adapter = _make_adapter()
        with pytest.raises(ValueError, match="is a comment, not a thread"):
            await adapter.read_thread("gh:peterdrier/ts4k/pull-comments/6001")

    @pytest.mark.asyncio
    async def test_reads_a_review_by_its_id(self):
        """A review ID returned from a thread read (gh:owner/repo/pulls/N/
        reviews/M) must resolve back through read_message rather than
        raising 'Unrecognised GitHub ID'."""
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(REVIEWS[0]))
        msg = await adapter.read_message("gh:peterdrier/ts4k/pulls/42/reviews/5001")
        assert adapter._client.get.call_args[0][0] == (
            "/repos/peterdrier/ts4k/pulls/42/reviews/5001"
        )
        assert msg["from"] == "alice"
        assert msg["id"] == "gh:peterdrier/ts4k/pulls/42/reviews/5001"

    @pytest.mark.asyncio
    async def test_review_id_is_not_a_thread(self):
        adapter = _make_adapter()
        with pytest.raises(ValueError, match="is a comment, not a thread"):
            await adapter.read_thread("gh:peterdrier/ts4k/pulls/42/reviews/5001")

    @pytest.mark.asyncio
    async def test_bad_id_raises(self):
        adapter = _make_adapter()
        with pytest.raises(ValueError, match="Unrecognised GitHub ID"):
            await adapter.read_message("gh:nonsense")


REVIEWS = [
    {
        "id": 5001,
        "user": {"login": "alice"},
        "state": "APPROVED",
        "body": "",
        "submitted_at": "2026-08-02T12:00:00Z",
        "html_url": "https://github.com/peterdrier/ts4k/pull/42#pullrequestreview-5001",
    },
    {
        "id": 5002,
        "user": {"login": "bob"},
        "state": "CHANGES_REQUESTED",
        "body": "Please add a test.",
        "submitted_at": "2026-08-02T14:00:00Z",
        "html_url": "https://github.com/peterdrier/ts4k/pull/42#pullrequestreview-5002",
    },
]

REVIEW_COMMENTS = [
    {
        "id": 6001,
        "user": {"login": "bob"},
        "created_at": "2026-08-02T13:00:00Z",
        "body": "Missing null check here.",
        "html_url": "https://github.com/peterdrier/ts4k/pull/42#discussion_r6001",
    },
]


class TestReadThread:
    @pytest.mark.asyncio
    async def test_issue_plus_comments(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[_mock_response(ISSUE), _mock_response(COMMENTS)]
        )
        thread = await adapter.read_thread("gh:peterdrier/ts4k#42")
        assert thread["thread_id"] == "gh:peterdrier/ts4k#42"
        assert thread["subject"] == "Add GitHub adapter"
        assert thread["message_count"] == 3
        assert [m["from"] for m in thread["messages"]] == ["peterdrier", "alice", "bob"]

    @pytest.mark.asyncio
    async def test_issue_thread_makes_no_pull_request_calls(self):
        """A plain issue has no /pulls endpoints — read_thread must not probe them."""
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[_mock_response(ISSUE), _mock_response(COMMENTS)]
        )
        await adapter.read_thread("gh:peterdrier/ts4k#42")
        assert adapter._client.get.call_count == 2
        paths = [c[0][0] for c in adapter._client.get.call_args_list]
        assert not any("/pulls/" in p for p in paths)

    @pytest.mark.asyncio
    async def test_pull_request_thread_includes_reviews_and_review_comments(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[
                _mock_response(PULL_REQUEST),
                _mock_response(COMMENTS),
                _mock_response(REVIEWS),
                _mock_response(REVIEW_COMMENTS),
            ]
        )
        thread = await adapter.read_thread("gh:peterdrier/ts4k#42")

        # Chronological across all three endpoints: alice's issue comment
        # (10:00), alice's approval (12:00), bob's inline review comment
        # (13:00), bob's changes-requested review (14:00), bob's issue
        # comment the next day (11:00).
        froms = [m["from"] for m in thread["messages"]]
        assert froms == ["peterdrier", "alice", "alice", "bob", "bob", "bob"]
        assert thread["message_count"] == 6

        bodies = [m["body"] for m in thread["messages"]]
        # A bare approval has no prose — the state is the whole message.
        assert "[APPROVED]" in bodies[2]
        assert bodies[3] == "Missing null check here."
        assert bodies[4] == "[CHANGES_REQUESTED] Please add a test."

        # Inline review comments get their own ID namespace so they read
        # back from /pulls/comments, not /issues/comments.
        assert thread["messages"][3]["id"] == "gh:peterdrier/ts4k/pull-comments/6001"

        # Review IDs carry the PR number so read_message can resolve them
        # back through /pulls/{number}/reviews/{id}.
        assert thread["messages"][2]["id"] == "gh:peterdrier/ts4k/pulls/42/reviews/5001"
        assert thread["messages"][4]["id"] == "gh:peterdrier/ts4k/pulls/42/reviews/5002"

    @pytest.mark.asyncio
    async def test_pull_request_calls_the_review_endpoints(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[
                _mock_response(PULL_REQUEST),
                _mock_response([]),
                _mock_response(REVIEWS),
                _mock_response([]),
            ]
        )
        await adapter.read_thread("gh:peterdrier/ts4k#42")
        paths = [c[0][0] for c in adapter._client.get.call_args_list]
        assert "/repos/peterdrier/ts4k/pulls/42/reviews" in paths
        assert "/repos/peterdrier/ts4k/pulls/42/comments" in paths

    @pytest.mark.asyncio
    async def test_paginates_past_100_reviews(self):
        """A PR with more than 100 submitted reviews must not silently lose
        the later ones to a single-page fetch — the inline review-comment
        endpoint already paginates; reviews need the same loop."""
        adapter = _make_adapter()
        full_page = [
            {
                "id": 9000 + i,
                "user": {"login": "carol"},
                "state": "APPROVED",
                "body": "",
                "submitted_at": "2026-08-02T12:00:00Z",
            }
            for i in range(100)
        ]
        adapter._client.get = AsyncMock(
            side_effect=[
                _mock_response(PULL_REQUEST),
                _mock_response([]),
                _mock_response(full_page),
                _mock_response(REVIEWS),  # second page, short — stops pagination
                _mock_response([]),
            ]
        )
        thread = await adapter.read_thread("gh:peterdrier/ts4k#42")
        # 100 from the first page + 2 from the short second page.
        assert thread["message_count"] == 1 + 100 + 2
        review_paths = [
            c[1]["params"]
            for c in adapter._client.get.call_args_list
            if c[0][0] == "/repos/peterdrier/ts4k/pulls/42/reviews"
        ]
        assert [p["page"] for p in review_paths] == [1, 2]

    @pytest.mark.asyncio
    async def test_empty_body_review_keeps_its_state_visible(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[
                _mock_response(PULL_REQUEST),
                _mock_response([]),
                _mock_response(REVIEWS),
                _mock_response([]),
            ]
        )
        thread = await adapter.read_thread("gh:peterdrier/ts4k#42")
        approved = next(m for m in thread["messages"] if "APPROVED" in m["body"])
        assert approved["body"] == "[APPROVED]"
        requested = next(m for m in thread["messages"] if "CHANGES_REQUESTED" in m["body"])
        assert requested["body"] == "[CHANGES_REQUESTED] Please add a test."

    @pytest.mark.asyncio
    async def test_pending_review_is_excluded(self):
        adapter = _make_adapter()
        pending = [{
            "id": 5003, "user": {"login": "carol"}, "state": "PENDING",
            "body": "", "submitted_at": "2026-08-02T15:00:00Z",
        }]
        adapter._client.get = AsyncMock(
            side_effect=[
                _mock_response(PULL_REQUEST),
                _mock_response([]),
                _mock_response(pending),
                _mock_response([]),
            ]
        )
        thread = await adapter.read_thread("gh:peterdrier/ts4k#42")
        assert thread["message_count"] == 1

    @pytest.mark.asyncio
    async def test_replies_are_chronologically_merged(self):
        """Conversation comments, reviews, and review comments are merged
        into a single date-ordered sequence rather than left as separate
        blocks in endpoint order."""
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[
                _mock_response(PULL_REQUEST),
                _mock_response(COMMENTS),  # 10:00, 11:00
                _mock_response(REVIEWS),  # 12:00, 14:00
                _mock_response(REVIEW_COMMENTS),  # 13:00
            ]
        )
        thread = await adapter.read_thread("gh:peterdrier/ts4k#42")
        dates = [m["date"] for m in thread["messages"][1:]]
        assert dates == sorted(dates)

    @pytest.mark.asyncio
    async def test_comment_ids_are_unique_and_native(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[_mock_response(ISSUE), _mock_response(COMMENTS)]
        )
        thread = await adapter.read_thread("gh:peterdrier/ts4k#42")
        assert [m["id"] for m in thread["messages"][1:]] == [
            "gh:peterdrier/ts4k/comments/991",
            "gh:peterdrier/ts4k/comments/992",
        ]

    @pytest.mark.asyncio
    async def test_no_comments(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[_mock_response(ISSUE), _mock_response([])]
        )
        thread = await adapter.read_thread("gh:peterdrier/ts4k#42")
        assert thread["message_count"] == 1

    @pytest.mark.asyncio
    async def test_resolves_notification_id(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            side_effect=[
                _mock_response(_notification()),
                _mock_response(ISSUE),
                _mock_response([]),
            ]
        )
        thread = await adapter.read_thread("gh:2984571234")
        assert thread["thread_id"] == "gh:peterdrier/ts4k#42"

    @pytest.mark.asyncio
    async def test_comment_id_rejected(self):
        adapter = _make_adapter()
        with pytest.raises(ValueError, match="is a comment, not a thread"):
            await adapter.read_thread("gh:peterdrier/ts4k/comments/991")


# ---------------------------------------------------------------------------
# mark_read — MUTATING.  Mocked transport only; never a live call.
# ---------------------------------------------------------------------------


class TestMarkRead:
    @pytest.mark.asyncio
    async def test_patches_the_notification_thread(self):
        adapter = _make_adapter(level="modify")
        adapter._client.patch = AsyncMock(
            return_value=_mock_response(None, status_code=205)
        )
        result = await adapter.mark_read("gh:2984571234")
        adapter._client.patch.assert_awaited_once_with(
            "/notifications/threads/2984571234"
        )
        assert result == {"id": "gh:2984571234", "status": "marked_read"}

    @pytest.mark.asyncio
    async def test_readonly_source_is_refused(self):
        adapter = _make_adapter()
        adapter._client.patch = AsyncMock()
        with pytest.raises(PermissionError):
            await adapter.mark_read("gh:2984571234")
        adapter._client.patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_issue_id_is_refused(self):
        adapter = _make_adapter(level="modify")
        adapter._client.patch = AsyncMock()
        with pytest.raises(ValueError, match="not a notification"):
            await adapter.mark_read("gh:peterdrier/ts4k#42")
        adapter._client.patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limited_patch_raises(self):
        adapter = _make_adapter(level="modify")
        adapter._client.patch = AsyncMock(
            return_value=_mock_response(
                None, status_code=403, headers={"X-RateLimit-Remaining": "0"}
            )
        )
        with pytest.raises(RuntimeError, match="rate limit"):
            await adapter.mark_read("gh:2984571234")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrors:
    @pytest.mark.asyncio
    async def test_401_names_the_token(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(None, status_code=401)
        )
        with pytest.raises(RuntimeError, match="rejected the token"):
            await adapter.whatsnew()

    @pytest.mark.asyncio
    async def test_rate_limit_reports_reset_time(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(
                None,
                status_code=403,
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1786000000"},
            )
        )
        with pytest.raises(RuntimeError, match="rate limit exceeded .resets 20"):
            await adapter.whatsnew()

    @pytest.mark.asyncio
    async def test_429_without_remaining_header_is_a_plain_http_error(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(None, status_code=429)
        )
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.whatsnew()

    @pytest.mark.asyncio
    async def test_403_that_is_not_a_rate_limit_is_a_plain_http_error(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(
                None, status_code=403, headers={"X-RateLimit-Remaining": "42"}
            )
        )
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.whatsnew()

    @pytest.mark.asyncio
    async def test_404_raises(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(
            return_value=_mock_response(None, status_code=404)
        )
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.read_message("gh:peterdrier/ts4k#999")

    @pytest.mark.asyncio
    async def test_non_json_body_raises_a_named_error(self):
        adapter = _make_adapter()
        adapter._client.get = AsyncMock(return_value=_mock_response(json_error=True))
        with pytest.raises(RuntimeError, match="non-JSON response"):
            await adapter.whatsnew()

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        adapter = GitHubAdapter(GitHubAdapterConfig(token="x"))
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.whatsnew()

    @pytest.mark.asyncio
    async def test_connect_without_token_raises(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        adapter = GitHubAdapter(GitHubAdapterConfig())
        with pytest.raises(RuntimeError, match="No GitHub token configured"):
            await adapter.connect()


class TestAdapterBasics:
    def test_default_prefix(self):
        assert GitHubAdapter(GitHubAdapterConfig(token="x")).source_prefix == "gh"

    def test_custom_prefix(self):
        adapter = GitHubAdapter(GitHubAdapterConfig(token="x"), prefix="work")
        assert adapter.source_prefix == "work"

    def test_default_level_is_readonly(self):
        from ts4k.core.levels import AccessLevel
        assert GitHubAdapter(GitHubAdapterConfig(token="x")).access_level == AccessLevel.READONLY

    @pytest.mark.asyncio
    async def test_connect_sets_auth_header(self, monkeypatch):
        adapter = GitHubAdapter(GitHubAdapterConfig(token="test-token-not-real"))
        await adapter.connect()
        try:
            assert adapter._client.headers["Authorization"] == "Bearer test-token-not-real"
            assert adapter._client.headers["X-GitHub-Api-Version"]
        finally:
            await adapter.disconnect()
        assert adapter._client is None

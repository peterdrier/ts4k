"""GitHub adapter — direct REST API client via httpx.

Calls ``api.github.com`` directly with a personal access token, following
the same shape as the O365 adapter (``httpx.AsyncClient`` built in
``connect()``, converters that turn API JSON into normalised ts4k dicts).

GitHub is a *notification* source, not a mailbox: ``whatsnew`` reads
``/notifications`` (the same feed as github.com/notifications) and each
row is classified into a category — ``ci``, ``review``, ``mention``,
``assignment``, ``dependabot``, ``release``, or the raw GitHub *reason*
when none of those fit.  Categories are suppressible through the normal
filter config (``ts4k filter add-category ci``).

ts4k reads GitHub, it never writes to it.  The only mutating call in this
adapter is ``mark_read``, which marks a *notification thread* as read —
it touches the notification inbox, never an issue, PR, or repository.

All IDs are native, in one of these forms::

    gh:2984571234                     a notification thread ID (from whatsnew)
    gh:owner/repo#42                  an issue or PR (from list_messages/search)
    gh:owner/repo/comments/9          an issue-style conversation comment (read_thread)
    gh:owner/repo/pulls/42/reviews/9  a PR review submission — approve/request-changes/comment
    gh:owner/repo/pull-comments/9     a PR inline review comment, on the diff (read_thread)

Review submissions and inline review comments are surfaced only when
``read_thread`` is called on a pull request — a plain issue has neither.

Usage::

    adapter = GitHubAdapter(GitHubAdapterConfig(token="<pat>"))
    async with adapter:
        notifications = await adapter.whatsnew()
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ts4k.adapters.base import BaseAdapter

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"

# Pinning the API version keeps response shapes stable across GitHub's
# rolling changes (https://docs.github.com/rest/overview/api-versions).
API_VERSION = "2022-11-28"

# The notifications endpoint caps per_page at 50; search caps at 100.
_NOTIFICATIONS_PAGE_SIZE = 50
_SEARCH_PAGE_SIZE = 100
_COMMENTS_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class GitHubAdapterConfig:
    """All knobs for the GitHub adapter."""

    token: str = ""
    """Personal access token.  Prefer *token_file* or the environment."""

    token_file: str | None = None
    """Path to a file holding the PAT — keeps it out of sources.json."""

    level: str | None = None
    """Access level: readonly (default), modify (allows mark_read)."""


def resolve_token(token: str | None = None, token_file: str | None = None) -> str:
    """Resolve the PAT, or ``""`` when none is configured.

    Order: explicit config value, config file, then ``GITHUB_TOKEN`` /
    ``GH_TOKEN`` — the two variables the ``gh`` CLI itself reads.  The
    token is never logged; only whether one was found.
    """
    if token and token.strip():
        return token.strip()
    if token_file and token_file.strip():
        from_file = _read_token_file(token_file.strip())
        if from_file:
            return from_file
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        env = os.environ.get(var, "").strip()
        if env:
            return env
    return ""


def _read_token_file(path: str) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("GitHub token file %s is unreadable", path)
        return ""


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

# GitHub's notification `reason` → ts4k category.  Reasons with no entry
# here pass through unchanged (comment, author, subscribed, state_change,
# ...), so nothing is silently collapsed into a bucket it doesn't belong in.
_REASON_CATEGORIES = {
    "ci_activity": "ci",
    "review_requested": "review",
    "approval_requested": "review",
    "mention": "mention",
    "team_mention": "mention",
    "assign": "assignment",
    "security_alert": "dependabot",
    "security_advisory_credit": "dependabot",
}

# `subject.type` → category, checked before the reason map: a release or a
# check-suite notification is that regardless of why it was delivered.
_SUBJECT_CATEGORIES = {
    "Release": "release",
    "CheckSuite": "ci",
    "RepositoryVulnerabilityAlert": "dependabot",
}

# Dependabot's PR titles use a fixed template ("Bump foo from 1.0 to 1.1").
# The notification payload carries no author, so the title is the only
# signal available without spending an extra API call per row.
_DEPENDABOT_TITLE = re.compile(r"^Bump \S+ from \S+ to \S+", re.IGNORECASE)


def classify(notification: dict) -> str:
    """Return the ts4k category for a GitHub notification object."""
    subject = notification.get("subject") or {}
    subject_type = subject.get("type", "")

    category = _SUBJECT_CATEGORIES.get(subject_type)
    if category:
        return category

    if subject_type == "PullRequest" and _DEPENDABOT_TITLE.match(
        subject.get("title", "") or ""
    ):
        return "dependabot"

    reason = notification.get("reason", "") or ""
    return _REASON_CATEGORIES.get(reason, reason or "other")


# ---------------------------------------------------------------------------
# ID handling
# ---------------------------------------------------------------------------

_ISSUE_ID = re.compile(r"^([^/\s]+)/([^/#\s]+)#(\d+)$")
_COMMENT_ID = re.compile(r"^([^/\s]+)/([^/#\s]+)/comments/(\d+)$")
# Inline PR review comments are numbered in their own sequence and are only
# readable via /pulls/comments/{id}, so they need an ID distinct from an
# issue comment's — the two ranges overlap, and a shared shape would send
# one to the wrong endpoint.
_REVIEW_COMMENT_ID = re.compile(r"^([^/\s]+)/([^/#\s]+)/pull-comments/(\d+)$")
# A review submission's single-item endpoint is nested under its pull
# request (/pulls/{number}/reviews/{id}), so the ID must carry the PR
# number as well — unlike an issue comment or inline review comment, whose
# endpoints are addressable by comment ID alone.
_REVIEW_ID = re.compile(r"^([^/\s]+)/([^/#\s]+)/pulls/(\d+)/reviews/(\d+)$")
_NOTIFICATION_ID = re.compile(r"^\d+$")


def _strip_prefix(prefixed_id: str, prefix: str) -> str:
    """Remove the source prefix from an ID if present."""
    if prefixed_id.startswith(f"{prefix}:"):
        return prefixed_id[len(prefix) + 1:]
    return prefixed_id


def parse_id(raw_id: str) -> tuple[str, tuple[str, ...]]:
    """Classify a de-prefixed ID.

    Returns ``("notification", (id,))``, ``("issue", (owner, repo, number))``,
    ``("comment", (owner, repo, comment_id))``, ``("review_comment",
    (owner, repo, comment_id))`` or ``("review", (owner, repo, number,
    review_id))``.  Raises ``ValueError`` for anything else, so a mistyped
    ID fails loudly rather than being sent to GitHub as a guess.
    """
    if _NOTIFICATION_ID.match(raw_id):
        return "notification", (raw_id,)
    m = _REVIEW_COMMENT_ID.match(raw_id)
    if m:
        return "review_comment", m.groups()
    m = _REVIEW_ID.match(raw_id)
    if m:
        return "review", m.groups()
    m = _COMMENT_ID.match(raw_id)
    if m:
        return "comment", m.groups()
    m = _ISSUE_ID.match(raw_id)
    if m:
        return "issue", m.groups()
    raise ValueError(
        f"Unrecognised GitHub ID {raw_id!r} — expected a notification ID "
        "(gh:2984571234) or an issue/PR (gh:owner/repo#42)."
    )


def _repo_from_url(url: str) -> tuple[str, str]:
    """Extract ``(owner, repo)`` from any api.github.com repo URL."""
    parts = httpx.URL(url).path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "repos":
        return parts[1], parts[2]
    raise ValueError(f"Cannot derive a repository from {url!r}")


def _issue_ref_from_url(url: str) -> str:
    """Turn ``.../repos/o/r/pulls/42`` into ``o/r#42``.

    Only issue and PR URLs convert.  Releases, commits, and check suites
    have no issue number, and guessing one from the trailing path segment
    would build an ID pointing at an unrelated issue.
    """
    parts = httpx.URL(url).path.strip("/").split("/")
    if (
        len(parts) >= 5
        and parts[0] == "repos"
        and parts[3] in ("issues", "pulls")
        and parts[4].isdigit()
    ):
        return f"{parts[1]}/{parts[2]}#{parts[4]}"
    raise ValueError(f"Cannot derive an issue reference from {url!r}")


# ---------------------------------------------------------------------------
# Response converters
# ---------------------------------------------------------------------------


def _notification_to_dict(notification: dict, prefix: str) -> dict:
    """Convert one ``/notifications`` entry to a normalised header dict.

    ``from`` is the repository, which is what a GitHub notification is
    actually "from" — and it makes ``ts4k filter add-sender owner/repo``
    suppress a noisy repository with no new machinery.
    """
    raw_id = str(notification.get("id", ""))
    subject = notification.get("subject") or {}
    repo = (notification.get("repository") or {}).get("full_name", "")
    category = classify(notification)

    thread_id = ""
    subject_url = subject.get("url") or ""
    if subject_url:
        try:
            thread_id = f"{prefix}:{_issue_ref_from_url(subject_url)}"
        except ValueError:
            # Commit / Release / Discussion subjects have no issue number.
            thread_id = ""

    return {
        "id": f"{prefix}:{raw_id}",
        "raw_id": raw_id,
        "source": prefix,
        "thread_id": thread_id,
        "from": repo,
        # The category leads the subject so it is visible in every output
        # format without widening the pipe listing by a column.
        "subject": f"[{category}] {subject.get('title', '')}".strip(),
        "date": notification.get("updated_at", ""),
        "category": category,
        "unread": bool(notification.get("unread", False)),
    }


def _issue_to_dict(issue: dict, prefix: str, ref: str) -> dict:
    """Convert an issue or PR object to a normalised message dict."""
    return {
        "id": f"{prefix}:{ref}",
        "raw_id": ref,
        "source": prefix,
        "thread_id": f"{prefix}:{ref}",
        "from": (issue.get("user") or {}).get("login", ""),
        "subject": issue.get("title", ""),
        "date": issue.get("created_at", ""),
        "body": issue.get("body") or "",
        "state": issue.get("state", ""),
        "url": issue.get("html_url", ""),
        "comment_count": issue.get("comments", 0),
        "is_pull_request": "pull_request" in issue,
    }


def _comment_to_dict(
    comment: dict, prefix: str, repo: str, subject: str,
    segment: str = "comments",
) -> dict:
    """Convert an issue comment to a normalised message dict.

    Also used for pull-request inline review comments — both resources
    share the same ``id`` / ``user`` / ``body`` / ``created_at`` /
    ``html_url`` shape — but those pass ``segment="pull-comments"`` so the
    resulting ID reads back from the endpoint it actually came from.
    """
    cid = comment.get("id", "")
    return {
        "id": f"{prefix}:{repo}/{segment}/{cid}",
        "raw_id": f"{repo}/{segment}/{cid}",
        "source": prefix,
        "from": (comment.get("user") or {}).get("login", ""),
        "subject": subject,
        "date": comment.get("created_at", ""),
        "body": comment.get("body") or "",
        "url": comment.get("html_url", ""),
    }


def _review_to_dict(
    review: dict, prefix: str, repo: str, number: str, subject: str
) -> dict:
    """Convert a pull-request review submission to a normalised message dict.

    A review can carry no prose at all — a bare "Approve" or "Request
    changes" click — in which case the state, not the empty body, is the
    entire point of the notification. It is folded into the body as a
    bracketed tag, the same way notification categories are (see
    ``_notification_to_dict``), so it survives even the pipe format,
    which renders nothing but ``from``, ``date``, and ``body``.

    The ID carries *number* (the pull request it belongs to) because
    GitHub's single-review endpoint is nested under the PR
    (``/pulls/{pull_number}/reviews/{review_id}``) — a review ID with no PR
    number has nothing for ``read_message``/``parse_id`` to resolve.
    """
    rid = review.get("id", "")
    state = (review.get("state") or "").upper()
    body = review.get("body") or ""
    if state:
        body = f"[{state}] {body}".strip()
    return {
        "id": f"{prefix}:{repo}/pulls/{number}/reviews/{rid}",
        "raw_id": f"{repo}/pulls/{number}/reviews/{rid}",
        "source": prefix,
        "from": (review.get("user") or {}).get("login", ""),
        "subject": subject,
        "date": review.get("submitted_at", ""),
        "body": body,
        "url": review.get("html_url", ""),
    }


def _search_item_to_dict(item: dict, prefix: str) -> dict:
    """Convert a ``/search/issues`` hit to a normalised header dict."""
    try:
        ref = _issue_ref_from_url(item.get("url", ""))
    except ValueError:
        repo_url = item.get("repository_url", "")
        try:
            owner, repo = _repo_from_url(repo_url)
        except ValueError:
            return {}
        ref = f"{owner}/{repo}#{item.get('number', '')}"

    return {
        "id": f"{prefix}:{ref}",
        "raw_id": ref,
        "source": prefix,
        "thread_id": f"{prefix}:{ref}",
        "from": (item.get("user") or {}).get("login", ""),
        "subject": item.get("title", ""),
        "date": item.get("updated_at", "") or item.get("created_at", ""),
        "snippet": (item.get("body") or "")[:200],
        "state": item.get("state", ""),
        "is_pull_request": "pull_request" in item,
    }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class GitHubAdapter(BaseAdapter):
    """GitHub adapter using direct REST API calls via httpx."""

    def __init__(self, config: GitHubAdapterConfig, prefix: str = "gh") -> None:
        self._config = config
        self._prefix = prefix
        self._client: httpx.AsyncClient | None = None
        from ts4k.core.levels import parse_level
        self._access_level = parse_level(config.level)

    @property
    def source_prefix(self) -> str:
        return self._prefix

    # -- Lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        if self._client is not None:
            return

        token = resolve_token(self._config.token, self._config.token_file)
        if not token:
            from ts4k.core.levels import AccessLevel
            scope = (
                "Notifications: write"
                if self._access_level >= AccessLevel.MODIFY
                else "Notifications: read"
            )
            raise RuntimeError(
                f"No GitHub token configured. Create a fine-grained PAT with "
                f"{scope} (plus Contents/Issues: read for the repos "
                "you follow), then: ts4k src add "
                f"{self._prefix} github token_file=<path>"
            )

        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=httpx.Timeout(30.0),
        )
        logger.info("GitHubAdapter connected")

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("GitHubAdapter disconnected")

    async def __aenter__(self) -> GitHubAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "GitHubAdapter is not connected. Call connect() or use "
                "'async with adapter:' first."
            )
        return self._client

    # -- Transport ----------------------------------------------------------

    def _check(self, resp: httpx.Response) -> None:
        """Turn GitHub's error responses into actionable exceptions.

        A bad token and an exhausted rate limit are the two failures an
        operator actually hits, and ``raise_for_status`` names neither.
        Anything else falls through to httpx's own error.
        """
        status = resp.status_code
        if status == 401:
            raise RuntimeError(
                "GitHub rejected the token (401). Check that the PAT is "
                "current and has Notifications: read."
            )
        if status in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = resp.headers.get("X-RateLimit-Reset", "")
            when = ""
            if reset.isdigit():
                when = datetime.fromtimestamp(int(reset), timezone.utc).strftime(
                    " (resets %Y-%m-%dT%H:%M:%SZ)"
                )
            raise RuntimeError(f"GitHub rate limit exceeded{when}.")
        resp.raise_for_status()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET *path* and return the parsed JSON body."""
        client = self._require_client()
        resp = await client.get(path, params=params or None)
        self._check(resp)
        try:
            return resp.json()
        except ValueError as exc:
            # A proxy or a maintenance page can answer 200 with HTML; that
            # must not surface as a bare JSONDecodeError from deep inside.
            raise RuntimeError(
                f"GitHub returned a non-JSON response for {path}"
            ) from exc

    # -- BaseAdapter data methods -------------------------------------------

    async def whatsnew(
        self,
        since: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
        count: int = 200,
    ) -> list[dict]:
        """Return notifications updated since *since*.

        Only unread notifications are returned — that is GitHub's own
        default for this endpoint, and it is what makes ``mark_read``
        remove an item from the feed.

        *sender* and *domain* are email filters; a GitHub notification has
        no email sender, so a query scoped to one returns nothing here
        rather than flooding it with unrelated rows.

        Defaults *since* to the last 24 hours when not given — that default
        is specific to this "what's new" entry point; ``list_messages``'s
        queryless path calls ``_fetch_notifications`` directly to read the
        full unread feed without it.
        """
        if sender or domain:
            return []

        if not since:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            since = yesterday.strftime("%Y-%m-%dT%H:%M:%SZ")

        return await self._fetch_notifications(since, count)

    async def _fetch_notifications(self, since: str | None, count: int) -> list[dict]:
        """Page through ``/notifications``, optionally filtered by *since*.

        *since* is ``None`` when the caller wants the unread feed
        unfiltered by recency — GitHub's ``/notifications`` has no implicit
        time cutoff of its own; that only exists because ``whatsnew``
        chooses to apply one.
        """
        results: list[dict] = []
        page = 1
        while len(results) < count:
            params: dict[str, Any] = {
                # Explicit rather than relying on GitHub's default:
                # mark_read only removes an item from this feed while
                # it stays scoped to unread.
                "all": "false",
                "per_page": _NOTIFICATIONS_PAGE_SIZE,
                "page": page,
            }
            if since:
                params["since"] = since
            data = await self._get("/notifications", params)
            if not isinstance(data, list) or not data:
                break
            results.extend(_notification_to_dict(n, self._prefix) for n in data)
            if len(data) < _NOTIFICATIONS_PAGE_SIZE:
                break
            page += 1

        results.sort(key=lambda m: m.get("date", ""), reverse=True)
        return results[:count]

    async def list_messages(
        self,
        query: str | None = None,
        count: int = 20,
        page_token: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
    ) -> list[dict]:
        """Search issues and PRs, or list notifications when *query* is empty.

        *query* is GitHub search syntax (``repo:owner/name is:open``).
        *page_token* is a 1-based page number.
        """
        if sender or domain:
            return []

        if not query:
            # No query to search with — the notification feed is the only
            # thing GitHub can list without one. Fetch it directly rather
            # than through whatsnew(), which applies a 24-hour cutoff that
            # would make anything unread for longer silently disappear
            # from this listing.
            return await self._fetch_notifications(None, count)

        page = int(page_token) if page_token and page_token.isdigit() else 1
        per_page = min(count, _SEARCH_PAGE_SIZE)
        data = await self._get(
            "/search/issues",
            {
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": per_page,
                "page": page,
            },
        )

        items = data.get("items", []) if isinstance(data, dict) else []
        results = [_search_item_to_dict(i, self._prefix) for i in items]
        results = [r for r in results if r][:count]

        total = data.get("total_count", 0) if isinstance(data, dict) else 0
        # Use the actual page size, not the requested count: when count
        # exceeds GitHub's 100-item cap, per_page is capped but count isn't,
        # so comparing against count would understate how many pages remain
        # and drop the continuation token before all matches are fetched.
        if results and page * per_page < total:
            results[-1]["_next_page_token"] = str(page + 1)

        return results

    async def read_message(self, msg_id: str, prefer_html: bool = False) -> dict:
        """Read an issue, PR, or comment.

        *prefer_html* is ignored — GitHub bodies are markdown, and there is
        no alternative representation to choose between.
        """
        kind, parts = parse_id(_strip_prefix(msg_id, self._prefix))

        if kind == "notification":
            ref = await self._issue_ref_for_notification(parts[0])
            kind, parts = "issue", tuple(re.split(r"[/#]", ref))

        if kind in ("comment", "review_comment"):
            owner, repo, cid = parts
            review = kind == "review_comment"
            path = "pulls" if review else "issues"
            comment = await self._get(f"/repos/{owner}/{repo}/{path}/comments/{cid}")
            return _comment_to_dict(
                comment, self._prefix, f"{owner}/{repo}", "",
                segment="pull-comments" if review else "comments",
            )

        if kind == "review":
            owner, repo, number, rid = parts
            review_obj = await self._get(
                f"/repos/{owner}/{repo}/pulls/{number}/reviews/{rid}"
            )
            return _review_to_dict(review_obj, self._prefix, f"{owner}/{repo}", number, "")

        owner, repo, number = parts
        ref = f"{owner}/{repo}#{number}"
        issue = await self._get(f"/repos/{owner}/{repo}/issues/{number}")
        return _issue_to_dict(issue, self._prefix, ref)

    async def read_thread(self, thread_id: str) -> dict:
        """Read an issue or PR together with its comments.

        The issue body is the first message; comments follow in
        chronological order. For a pull request that also includes review
        submissions (approve / request-changes / comment verdicts) and
        inline review comments — those live under separate endpoints from
        the issue-style conversation comments, so without them a PR thread
        would silently omit reviewer feedback.
        """
        kind, parts = parse_id(_strip_prefix(thread_id, self._prefix))

        if kind == "notification":
            ref = await self._issue_ref_for_notification(parts[0])
            parts = tuple(re.split(r"[/#]", ref))
        elif kind in ("comment", "review_comment", "review"):
            raise ValueError(
                f"{thread_id!r} is a comment, not a thread — use the issue ID "
                "(gh:owner/repo#42)."
            )

        owner, repo, number = parts
        ref = f"{owner}/{repo}#{number}"

        issue = await self._get(f"/repos/{owner}/{repo}/issues/{number}")
        head = _issue_to_dict(issue, self._prefix, ref)
        subject = head.get("subject", "")

        replies: list[dict] = []
        page = 1
        while True:
            comments = await self._get(
                f"/repos/{owner}/{repo}/issues/{number}/comments",
                {"per_page": _COMMENTS_PAGE_SIZE, "page": page},
            )
            if not isinstance(comments, list) or not comments:
                break
            replies.extend(
                _comment_to_dict(c, self._prefix, f"{owner}/{repo}", subject)
                for c in comments
            )
            if len(comments) < _COMMENTS_PAGE_SIZE:
                break
            page += 1

        if head.get("is_pull_request"):
            replies.extend(
                await self._pull_request_feedback(owner, repo, number, subject)
            )
            # Comments, reviews, and review comments each arrive sorted
            # within their own endpoint but not against each other —
            # merge into one chronological conversation.
            replies.sort(key=lambda m: m.get("date", ""))

        messages = [head, *replies]

        return {
            "thread_id": f"{self._prefix}:{ref}",
            "subject": subject,
            "message_count": len(messages),
            "messages": messages,
        }

    async def _pull_request_feedback(
        self, owner: str, repo: str, number: str, subject: str
    ) -> list[dict]:
        """Fetch PR review submissions and inline review comments.

        Only called for pull requests — issues have neither endpoint.
        """
        feedback: list[dict] = []

        page = 1
        while True:
            reviews = await self._get(
                f"/repos/{owner}/{repo}/pulls/{number}/reviews",
                {"per_page": _COMMENTS_PAGE_SIZE, "page": page},
            )
            if not isinstance(reviews, list) or not reviews:
                break
            feedback.extend(
                _review_to_dict(r, self._prefix, f"{owner}/{repo}", number, subject)
                for r in reviews
                # PENDING is the reviewer's own draft, not yet submitted —
                # nothing to notify anyone about until it's submitted.
                if (r.get("state") or "").upper() != "PENDING"
            )
            if len(reviews) < _COMMENTS_PAGE_SIZE:
                break
            page += 1

        page = 1
        while True:
            review_comments = await self._get(
                f"/repos/{owner}/{repo}/pulls/{number}/comments",
                {"per_page": _COMMENTS_PAGE_SIZE, "page": page},
            )
            if not isinstance(review_comments, list) or not review_comments:
                break
            feedback.extend(
                _comment_to_dict(
                    c, self._prefix, f"{owner}/{repo}", subject,
                    segment="pull-comments",
                )
                for c in review_comments
            )
            if len(review_comments) < _COMMENTS_PAGE_SIZE:
                break
            page += 1

        return feedback

    async def _issue_ref_for_notification(self, notification_id: str) -> str:
        """Resolve a notification thread ID to its ``owner/repo#N`` subject."""
        data = await self._get(f"/notifications/threads/{notification_id}")
        subject = (data or {}).get("subject") or {}
        subject_url = subject.get("url") or ""
        try:
            return _issue_ref_from_url(subject_url)
        except ValueError as exc:
            # Commits, releases, check suites, and discussions are real
            # notification subjects with no issue behind them — discussions
            # in particular are GraphQL-only, so REST cannot read them.
            raise ValueError(
                f"Notification {notification_id} is a "
                f"{subject.get('type', 'non-issue')} — ts4k can list it but "
                "not read its body. Only issues and pull requests are readable."
            ) from exc

    # -- Management methods (require level >= MODIFY) -----------------------

    async def mark_read(self, msg_id: str) -> dict:
        """Mark a notification thread as read.

        This is the only call in this adapter that changes anything, and
        it changes only the notification inbox — the issue, its comments,
        and the repository are untouched.
        """
        from ts4k.core.levels import AccessLevel, check_level
        check_level(self._access_level, AccessLevel.MODIFY, "mark_read")

        raw_id = _strip_prefix(msg_id, self._prefix)
        kind, parts = parse_id(raw_id)
        if kind != "notification":
            raise ValueError(
                f"{msg_id!r} is an issue or comment, not a notification. "
                "mark_read needs the notification ID from whatsnew."
            )

        client = self._require_client()
        resp = await client.patch(f"/notifications/threads/{parts[0]}")
        self._check(resp)
        return {"id": f"{self._prefix}:{parts[0]}", "status": "marked_read"}

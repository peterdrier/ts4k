"""Generic HTTP notification source adapter (#31).

Polls any JSON endpoint that returns::

    {
        "notifications": [
            {"date": "...", "source": "...", "subject": "...",
             "link": "...", "unread": true, "id": "..."},
            ...
        ],
        "meters": [{"label": "...", "count": 5, "link": "..."}]
    }

``notifications`` map onto ts4k's normalized header format:

- ``date``    -> ``date``
- ``source``  -> prepended to ``subject`` (e.g. "ConsentReviewNeeded: ...")
                 and carried as ``from`` (there is no real sender)
- ``subject`` -> ``subject``
- ``link``    -> ``link`` (kept for deep-linking; not part of the base
                 header contract, but extra keys are fine — O365 does
                 the same with ``snippet``/``has_attachments``)
- ``unread``  -> ``unread``
- ``id``      -> used as-is if present, else synthesized from a
                 source+date hash

``meters`` are not messages — they're stashed via ``ts4k.state.meters``
so ``ts4k overview`` can surface them without a live call (see that
module for details).

Auth is a static header (or headers) set via source config, e.g.::

    ts4k src add h http url=https://example.com/api/notifications \\
        header="X-Api-Key: abc123"

Multiple headers: repeat ``header=`` on the CLI, or comma-separate them
in one value (``header="X-Api-Key: abc,Authorization: Bearer xyz"`` —
header *values* must not themselves contain commas).

There is no per-notification endpoint, so ``read_message``/``read_thread``
re-poll and locate the matching entry — the notification may have already
been resolved upstream by then, in which case a fresh ``ValueError`` is
raised.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ts4k.adapters.base import BaseAdapter
from ts4k.state import meters as meters_state

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class HTTPAdapterConfig:
    """All knobs for the generic HTTP notification adapter."""

    url: str = ""
    header: Any = None
    """Raw header config as stored in sources.json: a single
    ``"Name: value"`` string, or a list of them (repeated ``header=``
    flags). Each entry may itself be comma-separated for multiple
    headers in one flag."""
    timeout: float = 10.0


def _parse_headers(raw: Any) -> dict[str, str]:
    """Normalize the ``header`` config field into a header dict.

    Accepts a single ``"Name: value"`` string, a list of such strings
    (repeated ``header=`` flags), and comma-separated multiples within
    any one entry.
    """
    if not raw:
        return {}
    items = raw if isinstance(raw, list) else [raw]
    headers: dict[str, str] = {}
    for item in items:
        for part in str(item).split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            name, _, value = part.partition(":")
            name, value = name.strip(), value.strip()
            if name:
                headers[name] = value
    return headers


def _synthesize_id(notif: dict) -> str:
    """Derive a stable ID from source+date when the payload has none."""
    basis = f"{notif.get('source', '')}|{notif.get('date', '')}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def _notification_to_header(notif: dict, prefix: str) -> dict:
    """Convert one ``notifications[]`` entry to a normalized header dict."""
    raw_id = str(notif.get("id") or _synthesize_id(notif))
    category = notif.get("source", "")
    subject = notif.get("subject", "")
    full_subject = f"{category}: {subject}" if category else subject

    return {
        "id": f"{prefix}:{raw_id}",
        "raw_id": raw_id,
        "source": prefix,
        "thread_id": f"{prefix}:{raw_id}",
        "from": category,
        "subject": full_subject,
        "date": notif.get("date", ""),
        "link": notif.get("link", ""),
        "unread": bool(notif.get("unread", False)),
    }


def _strip_prefix(prefixed_id: str, prefix: str) -> str:
    """Remove the source prefix from an ID if present."""
    if prefixed_id.startswith(f"{prefix}:"):
        return prefixed_id[len(prefix) + 1:]
    return prefixed_id


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class HTTPAdapter(BaseAdapter):
    """Generic HTTP notification adapter — polls a JSON endpoint."""

    def __init__(self, config: HTTPAdapterConfig, prefix: str = "h") -> None:
        self._config = config
        self._prefix = prefix
        self._client: httpx.AsyncClient | None = None

    @property
    def source_prefix(self) -> str:
        return self._prefix

    # -- Lifecycle ------------------------------------------------------

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(timeout=self._config.timeout)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> HTTPAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "HTTPAdapter is not connected. Call connect() or use "
                "'async with adapter:' first."
            )
        return self._client

    # -- Polling ----------------------------------------------------------

    async def _poll(self) -> dict | None:
        """GET the configured endpoint. Returns ``None`` on any failure.

        Timeouts, auth failures (4xx/5xx), connection errors, and
        non-JSON bodies are all logged and swallowed here so one broken
        HTTP source never breaks other adapters (platform failures are
        isolated).
        """
        client = self._require_client()
        headers = _parse_headers(self._config.header)
        try:
            resp = await client.get(self._config.url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            logger.warning("[%s] HTTP source timed out: %s", self._prefix, exc)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "[%s] HTTP source returned %s: %s",
                self._prefix, exc.response.status_code, exc,
            )
            return None
        except httpx.RequestError as exc:
            logger.warning("[%s] HTTP source request failed: %s", self._prefix, exc)
            return None
        except ValueError as exc:
            # resp.json() raises this (json.JSONDecodeError is a ValueError
            # subclass) on a non-JSON body.
            logger.warning("[%s] HTTP source returned non-JSON body: %s", self._prefix, exc)
            return None

        if not isinstance(data, dict):
            logger.warning(
                "[%s] HTTP source returned unexpected shape: %s", self._prefix, type(data).__name__
            )
            return None

        meters_state.set_meters(self._prefix, data.get("meters", []) or [])
        return data

    # -- BaseAdapter data methods -------------------------------------------

    async def whatsnew(
        self,
        since: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
        count: int = 200,
    ) -> list[dict]:
        # Notifications have no sender concept — sender/domain are ignored.
        data = await self._poll()
        if not data:
            return []
        notifications = data.get("notifications", []) or []
        results = [_notification_to_header(n, self._prefix) for n in notifications]
        if since:
            results = [r for r in results if r.get("date", "") >= since]
        results.sort(key=lambda r: r.get("date", ""), reverse=True)
        return results[:count]

    async def list_messages(
        self,
        query: str | None = None,
        count: int = 20,
        page_token: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
    ) -> list[dict]:
        # Single-page endpoint — no server-side query/pagination support.
        data = await self._poll()
        if not data:
            return []
        notifications = data.get("notifications", []) or []
        results = [_notification_to_header(n, self._prefix) for n in notifications]
        if query:
            q = query.lower()
            results = [r for r in results if q in r.get("subject", "").lower()]
        results.sort(key=lambda r: r.get("date", ""), reverse=True)
        return results[:count]

    async def read_message(self, msg_id: str, prefer_html: bool = False) -> dict:
        raw_id = _strip_prefix(msg_id, self._prefix)
        data = await self._poll()
        notifications = (data or {}).get("notifications", []) or []
        for notif in notifications:
            candidate = str(notif.get("id") or _synthesize_id(notif))
            if candidate == raw_id:
                header = _notification_to_header(notif, self._prefix)
                body = header["subject"]
                if header.get("link"):
                    body = f"{body}\n\n{header['link']}"
                return {**header, "body": body}
        raise ValueError(
            f"Notification {msg_id!r} not found — it may already be resolved upstream."
        )

    async def read_thread(self, thread_id: str) -> dict:
        # Each notification is its own single-entry "thread".
        msg = await self.read_message(thread_id)
        return {
            "thread_id": msg["thread_id"],
            "subject": msg["subject"],
            "message_count": 1,
            "messages": [msg],
        }

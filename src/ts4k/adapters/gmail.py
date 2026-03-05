"""Gmail adapter — direct Google API via google-api-python-client.

Connects directly to the Gmail API, eliminating the MCP middleman.
Key efficiency wins:
- Batch metadata fetch: 1 list + 1 batch get instead of N+1 calls.
- Field-level selection: only request what we need.
- Structured JSON: no regex parsing of plain text.
- Snippet included in listings for free.

Usage::

    adapter = GmailAdapter(
        GmailAdapterConfig(user_email="alice@gmail.com"),
    )
    async with adapter:
        msgs = await adapter.list_messages("newer_than:1d")
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ts4k.adapters.base import BaseAdapter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class GmailAdapterConfig:
    """All knobs for the Gmail adapter in one place."""

    user_email: str
    """Google email used for authentication."""

    config_dir: Path | None = None
    """Override for credential directory (default: ~/.config/ts4k)."""


# ---------------------------------------------------------------------------
# Response converters — pure functions, easy to test
# ---------------------------------------------------------------------------


def _get_header(headers: list[dict], name: str) -> str:
    """Case-insensitive lookup of a header value from Gmail API headers list.

    Gmail API returns headers as ``[{"name": "Subject", "value": "..."}]``.
    """
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def _decode_body(payload: dict) -> str:
    """Extract body text from a Gmail API payload tree.

    Walks the multipart tree, preferring text/plain over text/html.
    Returns raw HTML for text/html parts — the normalize pipeline handles
    HTML-to-text conversion (avoiding double-processing).
    """
    mime_type = payload.get("mimeType", "")

    # Leaf node with data.
    body_data = payload.get("body", {}).get("data")
    if body_data and "multipart" not in mime_type:
        decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        # Return raw text (plain or HTML) — normalize pipeline converts HTML.
        return decoded

    # Multipart: walk parts.
    parts = payload.get("parts", [])
    if not parts:
        return ""

    # Prefer text/plain.
    for part in parts:
        if part.get("mimeType") == "text/plain":
            text = _decode_body(part)
            if text:
                return text

    # Fall back to text/html.
    for part in parts:
        if part.get("mimeType") == "text/html":
            text = _decode_body(part)
            if text:
                return text

    # Recurse into nested multipart parts.
    for part in parts:
        if "multipart" in part.get("mimeType", ""):
            text = _decode_body(part)
            if text:
                return text

    return ""


def _extract_attachments(payload: dict) -> list[dict]:
    """Extract attachment metadata from a Gmail API payload tree.

    Returns a list of dicts with ``filename``, ``mime_type``, ``size``.
    Skips inline parts (no filename).
    """
    attachments: list[dict] = []
    parts = payload.get("parts", [])

    for part in parts:
        filename = part.get("filename", "")
        if filename:
            body = part.get("body", {})
            attachments.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType", ""),
                    "size": body.get("size", 0),
                }
            )
        # Recurse into nested multipart.
        if "multipart" in part.get("mimeType", ""):
            attachments.extend(_extract_attachments(part))

    return attachments


def _internal_date_to_iso(internal_date: str | int | None) -> str:
    """Convert Gmail internalDate (epoch ms) to ISO-8601 string."""
    if internal_date is None:
        return ""
    try:
        epoch_ms = int(internal_date)
        dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError, OSError):
        return ""


def _msg_to_headers(msg: dict, prefix: str) -> dict:
    """Convert a Gmail API message (metadata format) to a listing dict.

    Returns dict with: id, thread_id, from, subject, date, snippet, source.
    """
    headers = msg.get("payload", {}).get("headers", [])
    msg_id = msg.get("id", "")
    thread_id = msg.get("threadId", "")

    size_estimate = msg.get("sizeEstimate")

    result = {
        "id": f"{prefix}:{msg_id}",
        "raw_id": msg_id,
        "thread_id": f"{prefix}:{thread_id}",
        "raw_thread_id": thread_id,
        "from": _get_header(headers, "From"),
        "subject": _get_header(headers, "Subject"),
        "date": _internal_date_to_iso(msg.get("internalDate")),
        "snippet": msg.get("snippet", ""),
        "source": prefix,
    }
    if size_estimate:
        from ts4k.core.format import estimate_size
        result["size"] = estimate_size(size_estimate)
    return result


def _msg_to_full(msg: dict, prefix: str) -> dict:
    """Convert a Gmail API message (full format) to a complete message dict.

    Returns dict with: id, from, subject, date, body, and optional
    to, cc, message_id, attachments.
    """
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    msg_id = msg.get("id", "")

    result: dict[str, Any] = {
        "id": f"{prefix}:{msg_id}",
        "raw_id": msg_id,
        "thread_id": f"{prefix}:{msg.get('threadId', '')}",
        "from": _get_header(headers, "From"),
        "subject": _get_header(headers, "Subject"),
        "date": _internal_date_to_iso(msg.get("internalDate")),
        "body": _decode_body(payload),
        "source": prefix,
    }

    to = _get_header(headers, "To")
    if to:
        result["to"] = to
    cc = _get_header(headers, "Cc")
    if cc:
        result["cc"] = cc
    message_id = _get_header(headers, "Message-ID")
    if not message_id:
        message_id = _get_header(headers, "Message-Id")
    if message_id:
        result["message_id"] = message_id

    attachments = _extract_attachments(payload)
    if attachments:
        result["attachments"] = attachments

    return result


def _thread_to_dict(thread: dict, prefix: str) -> dict:
    """Convert a Gmail API thread (full format) to a thread dict.

    Returns dict with: thread_id, subject, message_count, messages.
    """
    thread_id = thread.get("id", "")
    messages_raw = thread.get("messages", [])

    messages = []
    subject = ""
    for i, msg in enumerate(messages_raw):
        full = _msg_to_full(msg, prefix)
        full["index"] = i + 1
        if i == 0 and full.get("subject"):
            subject = full["subject"]
        messages.append(full)

    return {
        "thread_id": f"{prefix}:{thread_id}",
        "subject": subject,
        "message_count": len(messages),
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class GmailAdapter(BaseAdapter):
    """Gmail adapter using direct Google API calls."""

    def __init__(self, config: GmailAdapterConfig, prefix: str = "g") -> None:
        self._config = config
        self._prefix = prefix
        self._service = None

    # -- BaseAdapter properties/lifecycle -----------------------------------

    @property
    def source_prefix(self) -> str:
        return self._prefix

    async def connect(self) -> None:
        """Build Gmail API service via OAuth credentials."""
        if self._service is not None:
            return  # already connected

        from ts4k.auth.google import build_gmail_service

        self._service = await asyncio.to_thread(
            build_gmail_service,
            self._config.user_email,
            config_dir=self._config.config_dir,
        )
        logger.info("GmailAdapter connected for %s", self._config.user_email)

    async def disconnect(self) -> None:
        """Close the Gmail API service."""
        if self._service is not None:
            try:
                self._service.close()
            except Exception:
                pass
            self._service = None
            logger.info("GmailAdapter disconnected")

    # Context manager support ------------------------------------------------

    async def __aenter__(self) -> GmailAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # -- Internal helpers ----------------------------------------------------

    def _require_service(self):
        if self._service is None:
            raise RuntimeError(
                "GmailAdapter is not connected. Call connect() or use "
                "'async with adapter:' first."
            )
        return self._service

    # -- BaseAdapter data methods -------------------------------------------

    async def whatsnew(self, since: str | None = None) -> list[dict]:
        """Search for recent messages.

        Uses Gmail search syntax: ``newer_than:1d`` by default, or
        ``after:<epoch>`` when *since* is given.
        """
        if since:
            query = f"after:{since}"
        else:
            query = "newer_than:1d"
        return await self.list_messages(query=query)

    async def list_messages(
        self,
        query: str | None = None,
        count: int = 20,
        page_token: str | None = None,
    ) -> list[dict]:
        """Search Gmail and return a list of message-header dicts.

        Two-step efficient fetch:
        1. messages.list() -> IDs only
        2. Batch messages.get(format=metadata) -> headers + snippet for all IDs
        """
        service = self._require_service()

        # Step 1: Get message IDs.
        list_args: dict[str, Any] = {
            "userId": "me",
            "q": query or "in:inbox",
            "maxResults": count,
        }
        if page_token:
            list_args["pageToken"] = page_token

        list_result = await asyncio.to_thread(
            lambda: service.users().messages().list(**list_args).execute()
        )

        message_ids = [m["id"] for m in list_result.get("messages", [])]
        if not message_ids:
            return []

        # Step 2: Batch fetch metadata for all IDs.
        results: list[dict] = []
        errors: list[str] = []

        def _batch_callback(request_id, response, exception):
            if exception is not None:
                errors.append(f"{request_id}: {exception}")
            else:
                results.append(response)

        batch = service.new_batch_http_request(callback=_batch_callback)
        for msg_id in message_ids:
            batch.add(
                service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["Subject", "From", "Date", "To"],
                )
            )
        await asyncio.to_thread(batch.execute)

        if errors:
            for err in errors:
                logger.warning("Batch metadata fetch error: %s", err)

        # Convert to header dicts.
        header_dicts = [_msg_to_headers(msg, self._prefix) for msg in results]

        # Sort by date descending (internalDate order).
        header_dicts.sort(key=lambda m: m.get("date", ""), reverse=True)

        # Attach pagination token.
        next_token = list_result.get("nextPageToken")
        if next_token and header_dicts:
            header_dicts[-1]["_next_page_token"] = next_token

        return header_dicts

    async def read_message(self, msg_id: str) -> dict:
        """Fetch a single message by its ts4k prefixed ID (``g:XXXX``)."""
        service = self._require_service()
        raw_id = self._strip_prefix(msg_id)

        msg = await asyncio.to_thread(
            lambda: service.users().messages().get(
                userId="me",
                id=raw_id,
                format="full",
            ).execute()
        )
        return _msg_to_full(msg, self._prefix)

    async def read_thread(self, thread_id: str) -> dict:
        """Fetch a full thread by its ts4k prefixed ID (``g:XXXX``)."""
        service = self._require_service()
        raw_id = self._strip_prefix(thread_id)

        thread = await asyncio.to_thread(
            lambda: service.users().threads().get(
                userId="me",
                id=raw_id,
                format="full",
            ).execute()
        )
        return _thread_to_dict(thread, self._prefix)

    # -- Mailbox stats -------------------------------------------------------

    _STATS_LABELS = [
        ("INBOX", "Inbox"),
        ("CATEGORY_PERSONAL", "Primary"),
        ("CATEGORY_SOCIAL", "Social"),
        ("CATEGORY_PROMOTIONS", "Promotions"),
        ("CATEGORY_UPDATES", "Updates"),
        ("CATEGORY_FORUMS", "Forums"),
        ("SPAM", "Spam"),
        ("TRASH", "Trash"),
    ]

    async def mailbox_stats(self) -> dict | None:
        """Return live label counts via batch labels.get() calls."""
        service = self._require_service()

        results: dict[str, dict] = {}
        errors: list[str] = []

        def _batch_callback(request_id, response, exception):
            if exception is not None:
                errors.append(f"{request_id}: {exception}")
            else:
                results[request_id] = response

        batch = service.new_batch_http_request(callback=_batch_callback)
        for label_id, _display in self._STATS_LABELS:
            batch.add(
                service.users().labels().get(userId="me", id=label_id),
                request_id=label_id,
            )
        await asyncio.to_thread(batch.execute)

        if errors:
            for err in errors:
                logger.warning("Label stats fetch error: %s", err)

        labels = []
        for label_id, display_name in self._STATS_LABELS:
            if label_id in results:
                resp = results[label_id]
                labels.append({
                    "name": display_name,
                    "total": resp.get("messagesTotal", 0),
                    "unread": resp.get("messagesUnread", 0),
                })

        return {"provider": "gmail", "labels": labels}

    # -- Helpers -------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        """Remove the ``g:`` prefix if present, returning the raw ID."""
        if prefixed_id.startswith(f"{self.source_prefix}:"):
            return prefixed_id[len(self.source_prefix) + 1 :]
        return prefixed_id

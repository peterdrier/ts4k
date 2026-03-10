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

    level: str | None = None
    """Access level: readonly (default), modify, draft."""


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
        from ts4k.core.levels import parse_level
        self._access_level = parse_level(config.level)

    # -- BaseAdapter properties/lifecycle -----------------------------------

    @property
    def source_prefix(self) -> str:
        return self._prefix

    async def connect(self) -> None:
        """Build Gmail API service via OAuth credentials."""
        if self._service is not None:
            return  # already connected

        from ts4k.auth.google import build_gmail_service
        from ts4k.core.levels import scopes_for

        scopes = scopes_for("gmail", self._access_level)
        self._service = await asyncio.to_thread(
            build_gmail_service,
            self._config.user_email,
            config_dir=self._config.config_dir,
            scopes=scopes,
        )
        logger.info("GmailAdapter connected for %s (level=%s)",
                    self._config.user_email, self._access_level.name.lower())

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

    _BATCH_CHUNK_SIZE = 25
    _BATCH_PAUSE_SECS = 0.2
    _RETRY_PAUSE_SECS = 0.5

    # -- BaseAdapter data methods -------------------------------------------

    async def whatsnew(
        self,
        since: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
    ) -> list[dict]:
        """Search for recent messages.

        Uses Gmail search syntax: ``newer_than:1d`` by default, or
        ``after:<epoch>`` when *since* is given.
        """
        if since:
            query = f"after:{since}"
        else:
            query = "newer_than:1d"
        return await self.list_messages(query=query, sender=sender, domain=domain)

    async def list_messages(
        self,
        query: str | None = None,
        count: int = 20,
        page_token: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
    ) -> list[dict]:
        """Search Gmail and return a list of message-header dicts.

        Three-step efficient fetch:
        1. messages.list() -> IDs only
        2. Check cache — hits go straight to results
        3. Chunked batch messages.get(format=metadata) for cache misses
        """
        from ts4k.state import cache

        service = self._require_service()

        # Build effective query with sender/domain filters.
        parts = []
        if query:
            parts.append(query)
        if sender:
            parts.append(f"from:{sender}")
        elif domain:
            parts.append(f"from:@{domain}")
        effective_query = " ".join(parts) if parts else "in:inbox"

        # Step 1: Get message IDs.
        list_args: dict[str, Any] = {
            "userId": "me",
            "q": effective_query,
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

        # Step 2: Check cache — hits skip the batch fetch.
        header_dicts: list[dict] = []
        uncached_ids: list[str] = []

        for msg_id in message_ids:
            prefixed = f"{self._prefix}:{msg_id}"
            cached = cache.get_header(prefixed)
            if cached is not None:
                header_dicts.append(cached)
            else:
                uncached_ids.append(msg_id)

        # Step 3: Chunked batch fetch for cache misses.
        if uncached_ids:
            fetched = await self._chunked_batch_fetch(service, uncached_ids)
            header_dicts.extend(fetched)

        # Sort by date descending (internalDate order).
        header_dicts.sort(key=lambda m: m.get("date", ""), reverse=True)

        # Attach pagination token.
        next_token = list_result.get("nextPageToken")
        if next_token and header_dicts:
            header_dicts[-1]["_next_page_token"] = next_token

        return header_dicts

    async def _chunked_batch_fetch(
        self, service: Any, msg_ids: list[str]
    ) -> list[dict]:
        """Fetch message metadata in chunks, with retry on 429 errors.

        Splits *msg_ids* into chunks of ``_BATCH_CHUNK_SIZE``, pausing
        between chunks to stay under rate limits.  Messages that receive
        a 429 response are retried once after a longer pause.
        """
        all_headers: list[dict] = []

        for chunk_idx in range(0, len(msg_ids), self._BATCH_CHUNK_SIZE):
            if chunk_idx > 0:
                await asyncio.sleep(self._BATCH_PAUSE_SECS)

            chunk = msg_ids[chunk_idx : chunk_idx + self._BATCH_CHUNK_SIZE]
            headers, failed_ids = await self._batch_fetch_chunk(service, chunk)
            all_headers.extend(headers)

            # Retry 429-failed IDs once.
            if failed_ids:
                logger.info(
                    "Retrying %d message(s) after 429: %s",
                    len(failed_ids),
                    failed_ids,
                )
                await asyncio.sleep(self._RETRY_PAUSE_SECS)
                retry_headers, still_failed = await self._batch_fetch_chunk(
                    service, failed_ids
                )
                all_headers.extend(retry_headers)
                if still_failed:
                    logger.warning(
                        "Failed to fetch %d message(s) after retry: %s",
                        len(still_failed),
                        still_failed,
                    )

        return all_headers

    async def _batch_fetch_chunk(
        self, service: Any, msg_ids: list[str]
    ) -> tuple[list[dict], list[str]]:
        """Fetch a single batch chunk of message metadata.

        Returns ``(header_dicts, failed_ids)`` where *failed_ids* are
        message IDs that received a 429 response and should be retried.
        """
        results: list[dict] = []
        failed_ids: list[str] = []
        id_map: dict[str, str] = {}

        def _batch_callback(request_id, response, exception):
            if exception is not None:
                resp = getattr(exception, "resp", None)
                if resp is not None and resp.status == 429:
                    failed_ids.append(id_map[request_id])
                else:
                    logger.warning(
                        "Batch metadata fetch error for %s: %s",
                        id_map.get(request_id, request_id),
                        exception,
                    )
            else:
                results.append(response)

        batch = service.new_batch_http_request(callback=_batch_callback)
        for i, msg_id in enumerate(msg_ids):
            req_id = str(i)
            id_map[req_id] = msg_id
            batch.add(
                service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["Subject", "From", "Date", "To"],
                ),
                request_id=req_id,
            )
        await asyncio.to_thread(batch.execute)

        header_dicts = [_msg_to_headers(msg, self._prefix) for msg in results]
        return header_dicts, failed_ids

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

    # -- Management methods (require level >= MODIFY) -----------------------

    def _check_modify(self, operation: str) -> None:
        from ts4k.core.levels import AccessLevel, check_level
        check_level(self._access_level, AccessLevel.MODIFY, operation)

    async def archive_message(self, msg_id: str) -> dict:
        self._check_modify("archive")
        service = self._require_service()
        raw_id = self._strip_prefix(msg_id)
        await asyncio.to_thread(
            lambda: service.users().messages().modify(
                userId="me", id=raw_id,
                body={"removeLabelIds": ["INBOX"]}
            ).execute()
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "archived"}

    async def unarchive_message(self, msg_id: str) -> dict:
        self._check_modify("unarchive")
        service = self._require_service()
        raw_id = self._strip_prefix(msg_id)
        await asyncio.to_thread(
            lambda: service.users().messages().modify(
                userId="me", id=raw_id,
                body={"addLabelIds": ["INBOX"]}
            ).execute()
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "unarchived"}

    async def _resolve_label_id(self, label_name: str, create: bool = True) -> str:
        """Find a label ID by name, optionally creating it if missing."""
        service = self._require_service()
        result = await asyncio.to_thread(
            lambda: service.users().labels().list(userId="me").execute()
        )
        for lbl in result.get("labels", []):
            if lbl.get("name", "").lower() == label_name.lower():
                return lbl["id"]
        if not create:
            raise ValueError(f"Label {label_name!r} not found")
        new_label = await asyncio.to_thread(
            lambda: service.users().labels().create(
                userId="me",
                body={"name": label_name, "labelListVisibility": "labelShow",
                      "messageListVisibility": "show"}
            ).execute()
        )
        return new_label["id"]

    async def label_message(self, msg_id: str, label: str) -> dict:
        self._check_modify("label")
        service = self._require_service()
        raw_id = self._strip_prefix(msg_id)
        label_id = await self._resolve_label_id(label, create=True)
        await asyncio.to_thread(
            lambda: service.users().messages().modify(
                userId="me", id=raw_id,
                body={"addLabelIds": [label_id]}
            ).execute()
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "labeled", "label": label}

    async def unlabel_message(self, msg_id: str, label: str) -> dict:
        self._check_modify("unlabel")
        service = self._require_service()
        raw_id = self._strip_prefix(msg_id)
        label_id = await self._resolve_label_id(label, create=False)
        await asyncio.to_thread(
            lambda: service.users().messages().modify(
                userId="me", id=raw_id,
                body={"removeLabelIds": [label_id]}
            ).execute()
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "unlabeled", "label": label}

    async def mark_read(self, msg_id: str) -> dict:
        self._check_modify("mark_read")
        service = self._require_service()
        raw_id = self._strip_prefix(msg_id)
        await asyncio.to_thread(
            lambda: service.users().messages().modify(
                userId="me", id=raw_id,
                body={"removeLabelIds": ["UNREAD"]}
            ).execute()
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "marked_read"}

    async def mark_unread(self, msg_id: str) -> dict:
        self._check_modify("mark_unread")
        service = self._require_service()
        raw_id = self._strip_prefix(msg_id)
        await asyncio.to_thread(
            lambda: service.users().messages().modify(
                userId="me", id=raw_id,
                body={"addLabelIds": ["UNREAD"]}
            ).execute()
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "marked_unread"}

    async def trash_message(self, msg_id: str) -> dict:
        self._check_modify("trash")
        service = self._require_service()
        raw_id = self._strip_prefix(msg_id)
        await asyncio.to_thread(
            lambda: service.users().messages().trash(
                userId="me", id=raw_id,
            ).execute()
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "trashed"}

    async def list_labels(self) -> list[dict]:
        self._check_modify("list_labels")
        service = self._require_service()
        result = await asyncio.to_thread(
            lambda: service.users().labels().list(userId="me").execute()
        )
        return [
            {"id": lbl["id"], "name": lbl.get("name", ""), "type": lbl.get("type", "")}
            for lbl in result.get("labels", [])
        ]

    async def create_label(self, name: str) -> dict:
        self._check_modify("create_label")
        label_id = await self._resolve_label_id(name, create=True)
        return {"id": label_id, "name": name, "status": "created"}

    # -- Helpers -------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        """Remove the ``g:`` prefix if present, returning the raw ID."""
        if prefixed_id.startswith(f"{self.source_prefix}:"):
            return prefixed_id[len(self.source_prefix) + 1 :]
        return prefixed_id

"""O365 adapter — direct Microsoft Graph API client via httpx + MSAL.

Calls the Graph API directly using ``httpx.AsyncClient`` with tokens
from ``ts4k.auth.microsoft``.  No MCP subprocess, no Node.js middleman.

Supports both personal (``/me/``) and shared mailbox access.  When
``mailbox`` is set in config, the adapter targets ``/users/{mailbox}/``
instead of ``/me/``.

Multi-mailbox setup (client_id/tenant_id inherited from first source)::

    # sources.json
    {
        "o":  {"provider": "o365", "client_id": "<id>", "tenant_id": "<tid>"},
        "ow": {"provider": "o365", "client_id": "<id>", "tenant_id": "<tid>",
               "mailbox": "hello@example.org"},
        "os": {"provider": "o365", "client_id": "<id>", "tenant_id": "<tid>",
               "mailbox": "support@example.org"}
    }

Usage::

    adapter = O365Adapter(O365AdapterConfig(
        client_id="<app-registration-id>",
        tenant_id="<tenant-id>",
        mailbox="user@contoso.com",
    ))
    async with adapter:
        msgs = await adapter.whatsnew()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ts4k.adapters.base import BaseAdapter

logger = logging.getLogger(__name__)

# Fields to request from Graph API via $select — keeps payloads small.
_LIST_SELECT = (
    "id,subject,from,receivedDateTime,bodyPreview,"
    "conversationId,hasAttachments,internetMessageId,isRead"
)
_READ_SELECT = (
    "id,subject,from,receivedDateTime,body,toRecipients,"
    "ccRecipients,conversationId,hasAttachments,internetMessageId"
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class O365AdapterConfig:
    """All knobs for the O365 adapter."""

    client_id: str = ""
    tenant_id: str = "common"
    mailbox: str | None = None
    """Target mailbox email.  When set, uses ``/users/{mailbox}/`` endpoint.
    When ``None``, uses ``/me/`` (primary mailbox)."""
    config_dir: Path | None = None

    level: str | None = None
    """Access level: readonly (default), modify, draft."""


# ---------------------------------------------------------------------------
# Response converters — dict from Graph API JSON
# ---------------------------------------------------------------------------


def _format_email_address(ea: dict) -> str:
    """Format ``{"name": "...", "address": "..."}`` as ``"Name <addr>"``."""
    name = ea.get("name", "")
    addr = ea.get("address", "")
    if name and addr:
        return f"{name} <{addr}>"
    return addr or name


def _format_from(msg: dict) -> str:
    """Extract the ``from`` field from a Graph API message."""
    from_obj = msg.get("from") or {}
    ea = from_obj.get("emailAddress") or from_obj
    return _format_email_address(ea)


def _format_recipients(recipients: list[dict]) -> str:
    """Format a list of Graph API recipient objects as comma-separated."""
    parts = []
    for r in recipients:
        ea = r.get("emailAddress", r)
        parts.append(_format_email_address(ea))
    return ", ".join(parts)


def _list_response_to_dicts(data: dict | list, prefix: str) -> list[dict]:
    """Convert a Graph API list response (dict or list) to normalised dicts.

    Accepts ``{"value": [...]}`` wrapping or a bare array ``[...]``.
    """
    items = data.get("value", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    results: list[dict] = []
    for msg in items:
        raw_id = msg.get("id", "")
        conv_id = msg.get("conversationId", "")

        results.append({
            "id": f"{prefix}:{raw_id}",
            "raw_id": raw_id,
            "source": prefix,
            "thread_id": f"{prefix}:{conv_id}" if conv_id else "",
            "from": _format_from(msg),
            "subject": msg.get("subject", ""),
            "date": msg.get("receivedDateTime", ""),
            "snippet": msg.get("bodyPreview", ""),
            "has_attachments": msg.get("hasAttachments", False),
            "unread": not msg.get("isRead", True),
        })

    return results


def _message_to_dict(data: dict, prefix: str) -> dict:
    """Convert a single Graph API message dict to a normalised dict."""
    if not data:
        return {}

    # Handle wrapped responses (sometimes {"value": {...}})
    if "value" in data and isinstance(data["value"], dict):
        data = data["value"]

    raw_id = data.get("id", "")
    conv_id = data.get("conversationId", "")

    # Body: prefer body.content for full read
    body_obj = data.get("body") or {}
    body = body_obj.get("content", "") if isinstance(body_obj, dict) else str(body_obj)
    # Fall back to bodyPreview if body.content is empty
    if not body:
        body = data.get("bodyPreview", "")

    result: dict[str, Any] = {
        "id": f"{prefix}:{raw_id}",
        "raw_id": raw_id,
        "source": prefix,
        "thread_id": f"{prefix}:{conv_id}" if conv_id else "",
        "from": _format_from(data),
        "subject": data.get("subject", ""),
        "date": data.get("receivedDateTime", ""),
        "body": body,
        "has_attachments": data.get("hasAttachments", False),
    }

    to_recip = data.get("toRecipients")
    if to_recip:
        result["to"] = _format_recipients(to_recip)

    cc_recip = data.get("ccRecipients")
    if cc_recip:
        result["cc"] = _format_recipients(cc_recip)

    msg_id = data.get("internetMessageId")
    if msg_id:
        result["message_id"] = msg_id

    return result


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def _build_sender_filter(sender: str | None) -> str | None:
    """Build a Graph API ``$filter`` clause for exact sender match."""
    if sender:
        return f"from/emailAddress/address eq '{sender}'"
    return None


def _build_domain_search(domain: str | None) -> str | None:
    """Build a Graph API ``$search`` clause for domain filtering.

    Graph API does not support ``endsWith`` on ``from/emailAddress/address``,
    so domain filtering must use KQL ``$search`` instead of ``$filter``.
    """
    if domain:
        return f'"from:@{domain}"'
    return None


class O365Adapter(BaseAdapter):
    """O365 adapter using direct Microsoft Graph API calls via httpx."""

    def __init__(self, config: O365AdapterConfig, prefix: str = "o") -> None:
        self._config = config
        self._prefix = prefix
        self._client: httpx.AsyncClient | None = None
        from ts4k.core.levels import parse_level
        self._access_level = parse_level(config.level)

    @property
    def source_prefix(self) -> str:
        return self._prefix

    # -- URL routing helper -------------------------------------------------

    def _base_url(self) -> str:
        """Return the base path for mailbox operations.

        Returns ``/me`` for personal mailbox, ``/users/{mailbox}`` for shared.
        """
        if self._config.mailbox:
            return f"/users/{self._config.mailbox}"
        return "/me"

    # -- Lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        if self._client is not None:
            return

        from ts4k.auth.microsoft import build_graph_client
        from ts4k.core.levels import scopes_for

        scopes = scopes_for("o365", self._access_level)
        self._client = await asyncio.to_thread(
            build_graph_client,
            self._config.client_id,
            tenant_id=self._config.tenant_id,
            config_dir=self._config.config_dir,
            username=self._config.mailbox,
            scopes=scopes,
        )
        mailbox_str = f" ({self._config.mailbox})" if self._config.mailbox else ""
        logger.info("O365Adapter connected via Graph API%s", mailbox_str)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("O365Adapter disconnected")

    async def __aenter__(self) -> O365Adapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "O365Adapter is not connected. Call connect() or use "
                "'async with adapter:' first."
            )
        return self._client

    async def _get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        """Issue a GET request to Graph API and return the parsed JSON."""
        client = self._require_client()
        kwargs: dict[str, Any] = {}
        if params:
            kwargs["params"] = params
        if headers:
            kwargs["headers"] = headers
        resp = await client.get(path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # -- BaseAdapter data methods -------------------------------------------

    async def whatsnew(
        self,
        since: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
        count: int = 200,
    ) -> list[dict]:
        since_specified = since is not None
        if not since:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            since = yesterday.strftime("%Y-%m-%dT%H:%M:%SZ")

        page_size = str(min(count, 200))

        # Domain filtering requires $search which can't combine with $filter.
        # When domain is used, do time filtering client-side.
        domain_search = _build_domain_search(domain)
        if domain_search:
            params: dict[str, str] = {
                "$search": domain_search,
                "$select": _LIST_SELECT,
                "$top": page_size,
            }
            headers = {"ConsistencyLevel": "eventual"}
            data = await self._get(
                f"{self._base_url()}/messages", params, headers=headers
            )
            results = _list_response_to_dicts(data, self.source_prefix)
            results.extend(
                await self._follow_next_links(data, count - len(results), headers)
            )
            # Client-side time filter only when caller specified a since value
            if since_specified:
                results = [m for m in results if m.get("date", "") >= since]
            results.sort(key=lambda m: m.get("date", ""), reverse=True)
            return results

        # Standard path: $filter for time + optional sender
        filter_parts = [f"receivedDateTime ge {since}"]
        sender_filter = _build_sender_filter(sender)
        if sender_filter:
            filter_parts.append(sender_filter)

        params = {
            "$filter": " and ".join(filter_parts),
            "$select": _LIST_SELECT,
            "$orderby": "receivedDateTime desc",
            "$top": page_size,
        }

        data = await self._get(f"{self._base_url()}/messages", params)
        results = _list_response_to_dicts(data, self.source_prefix)
        results.extend(await self._follow_next_links(data, count - len(results)))
        return results

    async def _follow_next_links(
        self,
        data: dict | list,
        needed: int,
        headers: dict[str, str] | None = None,
    ) -> list[dict]:
        """Follow ``@odata.nextLink`` pages until *needed* more results
        are collected or the server runs out of pages."""
        extra: list[dict] = []
        client = self._require_client()
        while needed > 0 and isinstance(data, dict):
            next_link = data.get("@odata.nextLink")
            if not next_link:
                break
            if headers:
                resp = await client.get(next_link, headers=headers)
            else:
                resp = await client.get(next_link)
            resp.raise_for_status()
            data = resp.json()
            page = _list_response_to_dicts(data, self.source_prefix)
            if not page:
                break
            extra.extend(page)
            needed -= len(page)
        return extra

    async def list_messages(
        self,
        query: str | None = None,
        count: int = 20,
        page_token: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
    ) -> list[dict]:
        # If page_token is a full URL (@odata.nextLink), use it directly.
        use_next_link = page_token and page_token.startswith("http")

        if use_next_link:
            # @odata.nextLink is a complete URL — call it as-is.
            client = self._require_client()
            headers = {"ConsistencyLevel": "eventual"}
            resp = await client.get(page_token, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        else:
            params: dict[str, str] = {
                "$select": _LIST_SELECT,
                "$top": str(count),
                "$orderby": "receivedDateTime desc",
            }

            if page_token:
                params["$skip"] = page_token

            # Sender-only filter via $filter (exact match). Graph permits
            # $orderby alongside a from-filter only when the orderby property
            # leads the $filter, so anchor it with a date floor — dropping
            # $orderby instead returns oldest-first and small $top values
            # truncate to the wrong end.
            sender_filter = _build_sender_filter(sender)
            if sender_filter and not query:
                params["$filter"] = (
                    f"receivedDateTime ge 1900-01-01T00:00:00Z and {sender_filter}"
                )

            headers_dict: dict[str, str] | None = None

            # Domain filter via $search (endsWith not supported on from address)
            domain_search = _build_domain_search(domain)

            # $search cannot combine with $filter or $orderby (400), and
            # requires the ConsistencyLevel header. A sender combined with a
            # free-text query therefore folds into the KQL string instead of
            # using $filter.
            if query and sender:
                params["$search"] = f'"from:{sender} {query}"'
            elif query and domain_search:
                params["$search"] = f'"from:@{domain} {query}"'
            elif query:
                params["$search"] = f'"{query}"'
            elif domain_search:
                params["$search"] = domain_search

            if "$search" in params:
                headers_dict = {"ConsistencyLevel": "eventual"}
                params.pop("$orderby", None)
                # $skip is incompatible with $search — use @odata.nextLink instead
                params.pop("$skip", None)

            data = await self._get(
                f"{self._base_url()}/messages", params, headers=headers_dict
            )

        results = _list_response_to_dicts(data, self.source_prefix)

        # Client-side sort when $orderby was removed ($search paths only —
        # the sender-only $filter path keeps server-side ordering)
        needs_sort = use_next_link or bool(query or _build_domain_search(domain))
        if needs_sort:
            results.sort(key=lambda m: m.get("date", ""), reverse=True)

        # Pagination: prefer @odata.nextLink, fall back to $skip offset
        next_link = data.get("@odata.nextLink") if isinstance(data, dict) else None
        if next_link:
            results[-1]["_next_page_token"] = next_link
        elif results and len(results) == count and not use_next_link:
            next_offset = int(page_token or 0) + len(results)
            results[-1]["_next_page_token"] = str(next_offset)

        return results

    async def read_message(self, msg_id: str, prefer_html: bool = False) -> dict:
        # Graph API's body.content is already HTML by default — nothing to switch.
        raw_id = _strip_prefix(msg_id, self.source_prefix)

        params = {"$select": _READ_SELECT}

        data = await self._get(
            f"{self._base_url()}/messages/{raw_id}", params
        )
        return _message_to_dict(data, self.source_prefix)

    async def read_thread(self, thread_id: str) -> dict:
        """Read an O365 conversation by conversationId.

        O365 groups related messages by ``conversationId``.  We fetch
        all messages with that conversation ID and return them sorted
        chronologically.
        """
        raw_id = _strip_prefix(thread_id, self.source_prefix)

        params = {
            "$filter": f"conversationId eq '{raw_id}'",
            "$select": _READ_SELECT,
        }
        # $filter on conversationId requires ConsistencyLevel and is
        # incompatible with $orderby — sort client-side instead.
        headers = {"ConsistencyLevel": "eventual"}

        data = await self._get(
            f"{self._base_url()}/messages", params, headers=headers
        )

        items = data.get("value", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []

        messages = [_message_to_dict(m, self.source_prefix) for m in items]
        messages.sort(key=lambda m: m.get("date", ""))
        subject = messages[0].get("subject", "") if messages else ""

        return {
            "thread_id": f"{self.source_prefix}:{raw_id}",
            "subject": subject,
            "message_count": len(messages),
            "messages": messages,
        }

    # -- Mailbox stats ------------------------------------------------------

    _FOLDER_NAMES = {"Inbox", "Sent Items", "Drafts", "Junk Email", "Deleted Items"}

    async def mailbox_stats(self) -> dict | None:
        """Return live folder counts via mailFolders API."""
        data = await self._get(
            f"{self._base_url()}/mailFolders",
            {"$top": "50"},
        )

        items = data.get("value", []) if isinstance(data, dict) else []
        labels = []
        for folder in items:
            name = folder.get("displayName", "")
            if name in self._FOLDER_NAMES:
                labels.append({
                    "name": name,
                    "total": folder.get("totalItemCount", 0),
                    "unread": folder.get("unreadItemCount", 0),
                })

        # Try Focused/Other via Inbox childFolders
        try:
            inbox_children = await self._get(
                f"{self._base_url()}/mailFolders/Inbox/childFolders",
                {"$top": "10"},
            )
            for child in inbox_children.get("value", []):
                name = child.get("displayName", "")
                if name in ("Focused", "Other"):
                    labels.append({
                        "name": name,
                        "total": child.get("totalItemCount", 0),
                        "unread": child.get("unreadItemCount", 0),
                    })
        except Exception:
            pass  # Focused Inbox not available

        return {"provider": "o365", "labels": labels}

    # -- Management methods (require level >= MODIFY) -----------------------

    def _check_modify(self, operation: str) -> None:
        from ts4k.core.levels import AccessLevel, check_level
        check_level(self._access_level, AccessLevel.MODIFY, operation)

    async def _post(self, path: str, json: dict | None = None) -> dict:
        """Issue a POST request to Graph API."""
        client = self._require_client()
        resp = await client.post(path, json=json or {})
        resp.raise_for_status()
        return resp.json()

    async def _patch(self, path: str, json: dict) -> dict:
        """Issue a PATCH request to Graph API."""
        client = self._require_client()
        resp = await client.patch(path, json=json)
        resp.raise_for_status()
        return resp.json()

    async def archive_message(self, msg_id: str) -> dict:
        self._check_modify("archive")
        raw_id = _strip_prefix(msg_id, self.source_prefix)
        await self._post(
            f"{self._base_url()}/messages/{raw_id}/move",
            json={"destinationId": "archive"},
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "archived"}

    async def unarchive_message(self, msg_id: str) -> dict:
        self._check_modify("unarchive")
        raw_id = _strip_prefix(msg_id, self.source_prefix)
        await self._post(
            f"{self._base_url()}/messages/{raw_id}/move",
            json={"destinationId": "inbox"},
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "unarchived"}

    async def mark_read(self, msg_id: str) -> dict:
        self._check_modify("mark_read")
        raw_id = _strip_prefix(msg_id, self.source_prefix)
        await self._patch(
            f"{self._base_url()}/messages/{raw_id}",
            json={"isRead": True},
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "marked_read"}

    async def mark_unread(self, msg_id: str) -> dict:
        self._check_modify("mark_unread")
        raw_id = _strip_prefix(msg_id, self.source_prefix)
        await self._patch(
            f"{self._base_url()}/messages/{raw_id}",
            json={"isRead": False},
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "marked_unread"}

    async def label_message(self, msg_id: str, label: str) -> dict:
        """Add a category to a message (O365 equivalent of Gmail labels)."""
        self._check_modify("categorize")
        raw_id = _strip_prefix(msg_id, self.source_prefix)
        # Get current categories, then add
        msg = await self._get(
            f"{self._base_url()}/messages/{raw_id}",
            {"$select": "categories"},
        )
        categories = list(msg.get("categories", []))
        if label not in categories:
            categories.append(label)
        await self._patch(
            f"{self._base_url()}/messages/{raw_id}",
            json={"categories": categories},
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "labeled", "label": label}

    async def unlabel_message(self, msg_id: str, label: str) -> dict:
        """Remove a category from a message."""
        self._check_modify("uncategorize")
        raw_id = _strip_prefix(msg_id, self.source_prefix)
        msg = await self._get(
            f"{self._base_url()}/messages/{raw_id}",
            {"$select": "categories"},
        )
        categories = [c for c in msg.get("categories", []) if c != label]
        await self._patch(
            f"{self._base_url()}/messages/{raw_id}",
            json={"categories": categories},
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "unlabeled", "label": label}

    async def _resolve_folder_id(self, folder_name: str, create: bool = True) -> str:
        """Find a folder ID by name, optionally creating it."""
        data = await self._get(
            f"{self._base_url()}/mailFolders",
            {"$filter": f"displayName eq '{folder_name.replace(chr(39), chr(39)*2)}'"},
        )
        folders = data.get("value", [])
        if folders:
            return folders[0]["id"]
        if not create:
            raise ValueError(f"Folder {folder_name!r} not found")
        result = await self._post(
            f"{self._base_url()}/mailFolders",
            json={"displayName": folder_name},
        )
        return result["id"]

    async def move_to_folder(self, msg_id: str, folder_name: str) -> dict:
        """Move a message to a named folder (creating it if needed)."""
        self._check_modify("move")
        raw_id = _strip_prefix(msg_id, self.source_prefix)
        folder_id = await self._resolve_folder_id(folder_name, create=True)
        await self._post(
            f"{self._base_url()}/messages/{raw_id}/move",
            json={"destinationId": folder_id},
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "moved", "folder": folder_name}

    async def trash_message(self, msg_id: str) -> dict:
        self._check_modify("trash")
        raw_id = _strip_prefix(msg_id, self.source_prefix)
        await self._post(
            f"{self._base_url()}/messages/{raw_id}/move",
            json={"destinationId": "deleteditems"},
        )
        return {"id": f"{self._prefix}:{raw_id}", "status": "trashed"}

    # -- Draft methods (require level >= DRAFT) -----------------------------

    def _check_draft(self, operation: str) -> None:
        from ts4k.core.levels import AccessLevel, check_level
        check_level(self._access_level, AccessLevel.DRAFT, operation)

    async def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to_message_id: str | None = None,
    ) -> dict:
        """Create a draft in the O365 Drafts folder. Does NOT send.

        Uses POST /me/messages which creates in Drafts by default.
        When reply_to_message_id is provided, sets conversationId for
        threading and blockquotes the original message body.
        """
        self._check_draft("create_draft")

        draft_body: dict = {
            "subject": subject,
            "body": {"contentType": "text", "content": body},
            "toRecipients": [
                {"emailAddress": {"address": to}}
            ],
        }

        if reply_to_message_id:
            raw_id = _strip_prefix(reply_to_message_id, self.source_prefix)
            orig = await self._get(
                f"{self._base_url()}/messages/{raw_id}",
                {"$select": "conversationId,subject,from,receivedDateTime,"
                            "body,internetMessageId"},
            )

            conv_id = orig.get("conversationId")
            if conv_id:
                draft_body["conversationId"] = conv_id

            # Auto-add Re: if not present
            if not subject.lower().startswith("re:"):
                orig_subject = orig.get("subject", "")
                draft_body["subject"] = f"Re: {orig_subject}" if orig_subject else subject

            # Build blockquote
            orig_from = _format_from(orig)
            orig_date = orig.get("receivedDateTime", "")
            orig_body_obj = orig.get("body", {})
            orig_body = orig_body_obj.get("content", "") if isinstance(orig_body_obj, dict) else ""
            if not orig_body:
                orig_body = orig.get("bodyPreview", "")

            # Strip HTML if body is HTML
            content_type = orig_body_obj.get("contentType", "text") if isinstance(orig_body_obj, dict) else "text"
            if content_type.lower() == "html" and orig_body:
                from ts4k.core.normalize import _html_to_text
                orig_body = _html_to_text(orig_body)

            quoted_lines = "\n".join(f"> {line}" for line in orig_body.strip().split("\n"))
            full_body = (
                f"{body}\n\n"
                f"On {orig_date}, {orig_from} wrote:\n"
                f"{quoted_lines}"
            )
            draft_body["body"]["content"] = full_body

            # Set In-Reply-To header for proper threading
            internet_msg_id = orig.get("internetMessageId")
            if internet_msg_id:
                draft_body["internetMessageHeaders"] = [
                    {"name": "In-Reply-To", "value": internet_msg_id},
                    {"name": "References", "value": internet_msg_id},
                ]

        result = await self._post(
            f"{self._base_url()}/messages",
            json=draft_body,
        )
        draft_id = result.get("id", "")
        return {"id": f"{self._prefix}:{draft_id}", "status": "draft_created"}

    # -- Discovery ----------------------------------------------------------

    async def discover_mailboxes(self) -> dict:
        """Query ``/me`` to discover available mailboxes.

        Returns a dict with ``primary``, ``aliases``, and ``display_name``.
        """
        data = await self._get("/me", {
            "$select": "mail,otherMails,proxyAddresses,displayName",
        })

        primary = data.get("mail", "")
        display_name = data.get("displayName", "")

        # otherMails is a simple list of email strings
        other_mails = data.get("otherMails") or []

        # proxyAddresses are like "SMTP:user@domain.com" or "smtp:alias@domain.com"
        proxy_addrs = data.get("proxyAddresses") or []
        aliases: list[str] = list(other_mails)
        for pa in proxy_addrs:
            # Strip the protocol prefix
            if ":" in pa:
                addr = pa.split(":", 1)[1]
            else:
                addr = pa
            addr_lower = addr.lower()
            if addr_lower != primary.lower() and addr_lower not in [a.lower() for a in aliases]:
                aliases.append(addr)

        return {
            "primary": primary,
            "aliases": aliases,
            "display_name": display_name,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_prefix(prefixed_id: str, prefix: str) -> str:
    """Remove the source prefix from an ID if present."""
    if prefixed_id.startswith(f"{prefix}:"):
        return prefixed_id[len(prefix) + 1:]
    return prefixed_id

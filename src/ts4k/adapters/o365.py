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
    "conversationId,hasAttachments,internetMessageId"
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
            "body": msg.get("bodyPreview", ""),
            "has_attachments": msg.get("hasAttachments", False),
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


class O365Adapter(BaseAdapter):
    """O365 adapter using direct Microsoft Graph API calls via httpx."""

    def __init__(self, config: O365AdapterConfig, prefix: str = "o") -> None:
        self._config = config
        self._prefix = prefix
        self._client: httpx.AsyncClient | None = None

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

        # build_graph_client is synchronous (MSAL token acquisition).
        self._client = await asyncio.to_thread(
            build_graph_client,
            self._config.client_id,
            tenant_id=self._config.tenant_id,
            config_dir=self._config.config_dir,
            username=self._config.mailbox,
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

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        """Issue a GET request to Graph API and return the parsed JSON."""
        client = self._require_client()
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    # -- BaseAdapter data methods -------------------------------------------

    async def whatsnew(self, since: str | None = None) -> list[dict]:
        if not since:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            since = yesterday.strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "$filter": f"receivedDateTime ge {since}",
            "$select": _LIST_SELECT,
            "$orderby": "receivedDateTime desc",
            "$top": "200",
        }

        data = await self._get(f"{self._base_url()}/messages", params)
        return _list_response_to_dicts(data, self.source_prefix)

    async def list_messages(
        self,
        query: str | None = None,
        count: int = 20,
        page_token: str | None = None,
    ) -> list[dict]:
        params: dict[str, str] = {
            "$select": _LIST_SELECT,
            "$top": str(count),
            "$orderby": "receivedDateTime desc",
        }

        if page_token:
            params["$skip"] = page_token

        if query:
            params["$search"] = f'"{query}"'

        data = await self._get(f"{self._base_url()}/messages", params)
        results = _list_response_to_dicts(data, self.source_prefix)

        # Attach next page token if we got a full page
        if results and len(results) == count:
            next_offset = int(page_token or 0) + len(results)
            results[-1]["_next_page_token"] = str(next_offset)

        return results

    async def read_message(self, msg_id: str) -> dict:
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
            "$orderby": "receivedDateTime asc",
        }

        data = await self._get(f"{self._base_url()}/messages", params)

        items = data.get("value", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []

        messages = [_message_to_dict(m, self.source_prefix) for m in items]
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

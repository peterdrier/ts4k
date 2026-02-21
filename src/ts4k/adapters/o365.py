"""O365 adapter — MCP client bridge to Softeria ms-365-mcp-server.

Connects to the ms-365-mcp-server (TypeScript, stdio) which wraps
Microsoft Graph API and returns JSON responses.

Supports both personal (``/me/``) and shared mailbox access.  When
``mailbox`` is set in config, the adapter uses shared mailbox tools
(``list-shared-mailbox-messages``, ``get-shared-mailbox-message``).
When ``None``, it uses standard ``/me/`` tools.

Multi-mailbox setup::

    # sources.json
    {
        "oa": {"provider": "o365", "mailbox": "user@contoso.com",
               "client_id": "<id>", "tenant_id": "<tid>"},
        "ob": {"provider": "o365", "mailbox": "hello@example.org"},
        "oh": {"provider": "o365", "mailbox": "support@example.org"}
    }

Usage::

    adapter = O365Adapter(O365AdapterConfig(
        mailbox="user@contoso.com",
        server_env={
            "MS365_MCP_CLIENT_ID": "<id>",
            "MS365_MCP_TENANT_ID": "<tid>",
        },
    ))
    async with adapter:
        msgs = await adapter.whatsnew()
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

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

    server_command: list[str] = field(
        default_factory=lambda: ["npx", "-y", "@softeria/ms-365-mcp-server"]
    )
    server_args: list[str] = field(
        default_factory=lambda: ["--preset", "mail", "--org-mode"]
    )
    server_cwd: str | None = None
    server_env: dict[str, str] | None = None

    mailbox: str | None = None
    """Target mailbox email.  When set, uses shared mailbox tools.
    When ``None``, uses ``/me/`` tools (primary mailbox)."""


# ---------------------------------------------------------------------------
# Response parsers — JSON from Graph API (via Softeria)
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


def parse_list_response(text: str, prefix: str) -> list[dict]:
    """Parse the JSON response from a list-mail-messages / list-shared-mailbox-messages call.

    Expects ``{"value": [...]}`` wrapping an array of Graph API messages,
    or a bare array ``[...]``.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse O365 list response as JSON")
        return []

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


def parse_message_response(text: str, prefix: str) -> dict:
    """Parse the JSON response from a get-mail-message / get-shared-mailbox-message call."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse O365 message response as JSON")
        return {}

    # Handle wrapped responses (sometimes the tool returns {"value": {...}})
    if isinstance(data, dict) and "value" in data and isinstance(data["value"], dict):
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
    """O365 adapter that bridges to ms-365-mcp-server via stdio MCP client."""

    def __init__(self, config: O365AdapterConfig, prefix: str = "o") -> None:
        self._config = config
        self._prefix = prefix
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    @property
    def source_prefix(self) -> str:
        return self._prefix

    # -- Tool routing helpers -----------------------------------------------

    def _tool_name(self, base: str) -> str:
        """Return the correct tool name based on mailbox config.

        ``base`` is one of ``"list-mail-messages"``, ``"get-mail-message"``.
        When ``mailbox`` is set, maps to shared-mailbox variants.
        """
        if self._config.mailbox:
            return base.replace("mail-message", "shared-mailbox-message")
        return base

    def _base_args(self) -> dict[str, str]:
        """Return base tool arguments (``user-id`` for shared mailbox, or empty)."""
        if self._config.mailbox:
            return {"userId": self._config.mailbox}
        return {}

    # -- Lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        if self._session is not None:
            return

        stack = AsyncExitStack()
        self._exit_stack = stack

        # Merge custom env vars into current environment (passing a bare dict
        # to StdioServerParameters replaces the entire env, breaking PATH etc.)
        merged_env: dict[str, str] | None = None
        if self._config.server_env:
            merged_env = {**os.environ, **self._config.server_env}

        params = StdioServerParameters(
            command=self._config.server_command[0],
            args=[
                *self._config.server_command[1:],
                *self._config.server_args,
            ],
            cwd=self._config.server_cwd,
            env=merged_env,
        )
        read_stream, write_stream = await stack.enter_async_context(
            stdio_client(params)
        )
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        self._session = session

        # Trigger login to ensure auth is ready
        try:
            await self._call_tool("login", {})
        except Exception as exc:
            logger.debug("Login call returned: %s (may be expected)", exc)
        mailbox_str = f" ({self._config.mailbox})" if self._config.mailbox else ""
        logger.info("O365Adapter connected via stdio%s", mailbox_str)

    async def disconnect(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            logger.info("O365Adapter disconnected")

    async def __aenter__(self) -> O365Adapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(
                "O365Adapter is not connected. Call connect() or use "
                "'async with adapter:' first."
            )
        return self._session

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        session = self._require_session()
        result = await session.call_tool(name, arguments)

        if result.isError:
            texts = [c.text for c in result.content if hasattr(c, "text")]
            raise RuntimeError(
                f"Upstream tool {name!r} returned error: "
                + ("; ".join(texts) or "(no details)")
            )

        texts = [c.text for c in result.content if hasattr(c, "text")]
        if not texts:
            raise RuntimeError(
                f"Upstream tool {name!r} returned no text content"
            )
        return "\n".join(texts)

    # -- BaseAdapter data methods -------------------------------------------

    async def whatsnew(self, since: str | None = None) -> list[dict]:
        if not since:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            since = yesterday.strftime("%Y-%m-%dT%H:%M:%SZ")

        args: dict[str, Any] = {
            **self._base_args(),
            "$filter": f"receivedDateTime ge {since}",
            "$select": _LIST_SELECT,
            "$orderby": "receivedDateTime desc",
        }

        tool = self._tool_name("list-mail-messages")
        text = await self._call_tool(tool, args)
        return parse_list_response(text, self.source_prefix)

    async def list_messages(
        self, query: str | None = None, count: int = 20
    ) -> list[dict]:
        args: dict[str, Any] = {
            **self._base_args(),
            "$select": _LIST_SELECT,
            "$top": str(count),
            "$orderby": "receivedDateTime desc",
        }

        if query:
            args["$search"] = f'"{query}"'

        tool = self._tool_name("list-mail-messages")
        text = await self._call_tool(tool, args)
        return parse_list_response(text, self.source_prefix)

    async def read_message(self, msg_id: str) -> dict:
        raw_id = self._strip_prefix(msg_id)

        args: dict[str, Any] = {
            **self._base_args(),
            "message-id": raw_id,
            "$select": _READ_SELECT,
        }

        tool = self._tool_name("get-mail-message")
        text = await self._call_tool(tool, args)
        return parse_message_response(text, self.source_prefix)

    async def read_thread(self, thread_id: str) -> dict:
        """Read an O365 conversation by conversationId.

        O365 groups related messages by ``conversationId``.  We fetch
        all messages with that conversation ID and return them sorted
        chronologically.
        """
        raw_id = self._strip_prefix(thread_id)

        args: dict[str, Any] = {
            **self._base_args(),
            "$filter": f"conversationId eq '{raw_id}'",
            "$select": _READ_SELECT,
            "$orderby": "receivedDateTime asc",
        }

        tool = self._tool_name("list-mail-messages")
        text = await self._call_tool(tool, args)

        # Parse as full messages (with body.content)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {
                "thread_id": f"{self.source_prefix}:{raw_id}",
                "subject": "",
                "message_count": 0,
                "messages": [],
            }

        items = data.get("value", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []

        messages = [parse_message_response(json.dumps(m), self.source_prefix) for m in items]
        subject = messages[0].get("subject", "") if messages else ""

        return {
            "thread_id": f"{self.source_prefix}:{raw_id}",
            "subject": subject,
            "message_count": len(messages),
            "messages": messages,
        }

    # -- Discovery ----------------------------------------------------------

    async def discover_mailboxes(self) -> dict:
        """Call get-current-user to discover available mailboxes.

        Returns a dict with ``primary``, ``aliases``, and ``display_name``.
        """
        text = await self._call_tool("get-current-user", {
            "$select": "mail,otherMails,proxyAddresses,displayName",
        })
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"primary": "", "aliases": [], "display_name": ""}

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

    # -- Helpers ------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        if prefixed_id.startswith(f"{self.source_prefix}:"):
            return prefixed_id[len(self.source_prefix) + 1:]
        return prefixed_id

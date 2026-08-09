"""WhatsApp adapter — MCP client for the whatsapp-mcp archive.

Two transports, same tool surface:

``http`` (default)
    Streamable HTTP MCP served directly by the Go bridge, behind the
    challenge-response auth in :mod:`ts4k.adapters.wa_bridge_auth`.  No
    subprocess.

``stdio``
    Spawns the Python FastMCP server (``whatsapp-mcp-server``) as a
    subprocess.  Retained as the documented rollback while that server still
    exists (whatsapp-mcp#85 retires it).

Usage::

    adapter = WhatsAppAdapter(WhatsAppAdapterConfig(
        bridge_url="http://127.0.0.1:18741/mcp",
        bridge_token_file="~/whatsapp-mcp/whatsapp-bridge/store/api_token",
    ))
    async with adapter:
        msgs = await adapter.list_messages(count=20)
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from ts4k.adapters import wa_bridge_auth
from ts4k.adapters.base import BaseAdapter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class WhatsAppAdapterConfig:
    """All knobs for the WhatsApp adapter."""

    # "http" talks to the Go bridge directly; "stdio" spawns the Python server.
    transport: str = "http"

    # -- http transport ------------------------------------------------------
    bridge_url: str = wa_bridge_auth.DEFAULT_BRIDGE_URL
    # The HMAC key the bridge mints at store/api_token on first run.  Either
    # the value or a path to it; both are optional here because the key also
    # resolves from WHATSAPP_API_TOKEN / WHATSAPP_API_TOKEN_FILE.
    bridge_token: str | None = None
    bridge_token_file: str | None = None

    # -- stdio transport (rollback) -----------------------------------------
    server_command: list[str] = field(default_factory=lambda: ["uv", "run", "main.py"])
    server_cwd: str | None = None
    server_env: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def _wa_msg_to_dict(msg: dict, prefix: str) -> dict:
    """Convert a structured message dict from upstream to ts4k format."""
    ts_raw = msg.get("timestamp", "")
    # Ensure ISO 8601 T separator
    ts_iso = ts_raw.replace(" ", "T", 1) if ts_raw and "T" not in ts_raw else ts_raw

    sender_jid = msg.get("sender_jid") or ""
    sender_name = msg.get("sender_name") or ""
    is_from_me = msg.get("is_from_me", False)

    # Upstream MCP resolves most names via its SQLite DB.
    # Fallback: ts4k contacts map, then chat_name for 1:1 chats, then raw JID.
    if not sender_name and sender_jid:
        from ts4k.state import contacts
        alias = contacts.resolve(f"{prefix}:{sender_jid}")
        if alias:
            sender_name = alias
        elif msg.get("chat_name") and not sender_jid.endswith("@g.us"):
            sender_name = msg["chat_name"]
        else:
            sender_name = sender_jid

    result: dict[str, Any] = {
        "id": f"{prefix}:{msg.get('id', '')}",
        "source": prefix,
        "from": sender_name,
        "subject": msg.get("chat_name") or "",
        "date": ts_iso,
        "body": msg.get("content") or "",
        "is_from_me": is_from_me,
    }

    if sender_jid:
        result["sender_jid"] = sender_jid
    if msg.get("chat_name"):
        result["chat_name"] = msg["chat_name"]
    if msg.get("chat_jid"):
        result["chat_jid"] = msg["chat_jid"]
    if msg.get("media_type"):
        result["media_type"] = msg["media_type"]

    return result


def parse_list_messages_response(text: str, prefix: str) -> list[dict]:
    """Parse structured JSON dicts from whatsapp-mcp list_messages."""
    items = _parse_ndjson(text)
    return [_wa_msg_to_dict(msg, prefix) for msg in items]


def _parse_ndjson(text: str) -> list[dict]:
    """Parse newline-delimited JSON (one JSON object per line).

    FastMCP returns each dataclass as a separate content block, which
    _call_tool joins with newlines.
    """
    items: list[dict] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, list):
                items.extend(obj)
            else:
                items.append(obj)
        except json.JSONDecodeError:
            continue
    return items


def parse_list_chats_response(text: str, prefix: str) -> list[dict]:
    """Parse the JSON response from whatsapp-mcp list_chats.

    FastMCP serializes each Chat dataclass as a separate content block.
    """
    data = _parse_ndjson(text)

    results: list[dict] = []
    for chat in data:
        jid = chat.get("jid", "")
        ts_raw = chat.get("last_message_time", "")
        ts_iso = ts_raw.replace(" ", "T", 1) if ts_raw and "T" not in ts_raw else ts_raw

        results.append({
            "id": f"{prefix}:{jid}",
            "raw_id": jid,
            "source": prefix,
            "from": chat.get("name") or jid,
            "subject": chat.get("name") or jid,
            "date": ts_iso,
            "body": chat.get("last_message") or "",
            "is_group": jid.endswith("@g.us"),
        })

    return results


def parse_message_context_response(text: str, prefix: str) -> dict:
    """Parse the JSON response from get_message_context."""
    items = _parse_ndjson(text)
    if not items:
        logger.warning("Failed to parse message_context response")
        return {}
    data = items[0]

    target = data.get("message", {})
    before = data.get("before", [])
    after = data.get("after", [])

    messages = [_wa_msg_to_dict(m, prefix) for m in reversed(before)]
    messages.append(_wa_msg_to_dict(target, prefix))
    messages.extend(_wa_msg_to_dict(m, prefix) for m in after)

    return {
        "thread_id": f"{prefix}:{target.get('chat_jid', '')}",
        "subject": target.get("chat_name", ""),
        "message_count": len(messages),
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def _flatten(exc: BaseException):
    """Yield the leaf exceptions of a (possibly nested) ExceptionGroup."""
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            yield from _flatten(sub)
    else:
        yield exc


class WhatsAppAdapter(BaseAdapter):
    """WhatsApp adapter that bridges to whatsapp-mcp via stdio MCP client."""

    def __init__(self, config: WhatsAppAdapterConfig, prefix: str = "w") -> None:
        self._config = config
        self._prefix = prefix
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    @property
    def source_prefix(self) -> str:
        return self._prefix

    async def connect(self) -> None:
        if self._session is not None:
            return

        stack = AsyncExitStack()
        self._exit_stack = stack
        try:
            if self._config.transport == "stdio":
                streams = await self._connect_stdio(stack)
            else:
                streams = await self._connect_http(stack)
            session = await stack.enter_async_context(ClientSession(*streams))
            await session.initialize()
        except BaseException as exc:
            # The HTTP transport runs inside an anyio task group: a failed
            # request cancels initialize(), and the error that explains why
            # only surfaces when the group unwinds — here.  Both halves have
            # to be considered or the operator is told "Cancelled via cancel
            # scope", which names nothing.
            teardown: BaseException | None = None
            try:
                await stack.aclose()
            except BaseException as close_exc:
                teardown = close_exc
            self._exit_stack = None
            raise self._connect_error(exc, teardown) from exc
        self._session = session
        logger.info("WhatsAppAdapter connected via %s", self._config.transport)

    def _connect_error(
        self, exc: BaseException, teardown: BaseException | None
    ) -> BaseException:
        """Pick the failure an operator can act on out of a task group's rubble."""
        leaves = [leaf for group in (exc, teardown) if group is not None
                  for leaf in _flatten(group)]
        cause = next(
            (leaf for leaf in leaves if not isinstance(leaf, asyncio.CancelledError)),
            leaves[0] if leaves else exc,
        )
        if getattr(getattr(cause, "response", None), "status_code", None) == 401:
            # The one failure that is all but guaranteed during rollout, and
            # the one httpx describes least usefully.
            return RuntimeError(
                f"WhatsApp bridge at {self._config.bridge_url} rejected our key "
                "(HTTP 401). Set bridge_token or bridge_token_file on the source "
                "to the contents of the bridge's store/api_token."
            )
        return cause

    async def _connect_stdio(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        params = StdioServerParameters(
            command=self._config.server_command[0],
            args=self._config.server_command[1:],
            cwd=self._config.server_cwd,
            env=self._config.server_env,
        )
        return await stack.enter_async_context(stdio_client(params))

    async def _connect_http(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        url = wa_bridge_auth.pin_loopback(self._config.bridge_url)
        token = wa_bridge_auth.resolve_bridge_token(
            self._config.bridge_token, self._config.bridge_token_file
        )
        if not token:
            # Not fatal — the bridge answers 401, which names the problem more
            # precisely than a guess here could — but silence would leave the
            # operator staring at an auth error with no hint where the key
            # comes from.
            logger.warning(
                "No WhatsApp bridge token configured; requests to %s will be "
                "rejected. Set bridge_token / bridge_token_file on the source, "
                "or WHATSAPP_API_TOKEN. The bridge mints it at store/api_token "
                "on first run.",
                url,
            )
        read_stream, write_stream, _get_session_id = await stack.enter_async_context(
            streamablehttp_client(
                url,
                httpx_client_factory=wa_bridge_auth.bridge_client_factory(token),
            )
        )
        return read_stream, write_stream

    async def disconnect(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            logger.info("WhatsAppAdapter disconnected")

    async def __aenter__(self) -> WhatsAppAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(
                "WhatsAppAdapter is not connected. Call connect() or use "
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
            if name in ("list_messages", "list_chats"):
                return ""
            raise RuntimeError(
                f"Upstream tool {name!r} returned no text content"
            )
        return "\n".join(texts)

    # -- BaseAdapter data methods -------------------------------------------

    async def whatsnew(
        self,
        since: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
        count: int = 200,
    ) -> list[dict]:
        # count is accepted for interface parity; the bridge caps at 50.
        args: dict[str, Any] = {"limit": 50, "include_context": False}
        if since:
            args["after"] = since
        else:
            # Default: last 24 hours
            from datetime import timedelta
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            args["after"] = yesterday.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        text = await self._call_tool("list_messages", args)
        return parse_list_messages_response(text, self.source_prefix)

    async def list_messages(
        self,
        query: str | None = None,
        count: int = 20,
        page_token: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
    ) -> list[dict]:
        if query and query.startswith("chat:"):
            # Direct chat JID query
            chat_jid = query[5:]
            text = await self._call_tool("list_messages", {
                "chat_jid": chat_jid,
                "limit": count,
                "include_context": False,
            })
            return parse_list_messages_response(text, self.source_prefix)

        if query:
            # Content search
            text = await self._call_tool("list_messages", {
                "query": query,
                "limit": count,
                "include_context": False,
            })
            return parse_list_messages_response(text, self.source_prefix)

        # Default: list recent chats (most useful overview)
        text = await self._call_tool("list_chats", {
            "limit": count,
            "include_last_message": True,
        })
        return parse_list_chats_response(text, self.source_prefix)

    async def read_message(self, msg_id: str) -> dict:
        raw_id = self._strip_prefix(msg_id)
        text = await self._call_tool("get_message_context", {
            "message_id": raw_id,
            "before": 0,
            "after": 0,
        })
        ctx = parse_message_context_response(text, self.source_prefix)
        if ctx.get("messages"):
            return ctx["messages"][0]
        raise RuntimeError(f"Message not found: {msg_id}")

    async def read_thread(self, thread_id: str) -> dict:
        """Read conversation context around a message or in a chat.

        WhatsApp doesn't have threads like Gmail. This returns messages
        around the given message ID (conversation context).
        """
        raw_id = self._strip_prefix(thread_id)

        # If it looks like a chat JID, list recent messages in that chat
        if "@" in raw_id:
            text = await self._call_tool("list_messages", {
                "chat_jid": raw_id,
                "limit": 20,
                "include_context": False,
            })
            msgs = parse_list_messages_response(text, self.source_prefix)
            # Reverse to chronological order (list_messages returns newest first)
            msgs.reverse()
            chat_text = await self._call_tool("get_chat", {"chat_jid": raw_id})
            try:
                chat_data = json.loads(chat_text)
                chat_name = chat_data.get("name", raw_id)
            except json.JSONDecodeError:
                chat_name = raw_id

            return {
                "thread_id": f"{self.source_prefix}:{raw_id}",
                "subject": chat_name,
                "message_count": len(msgs),
                "messages": msgs,
            }

        # Otherwise treat as message ID, get surrounding context
        text = await self._call_tool("get_message_context", {
            "message_id": raw_id,
            "before": 10,
            "after": 10,
        })
        return parse_message_context_response(text, self.source_prefix)

    def _strip_prefix(self, prefixed_id: str) -> str:
        if prefixed_id.startswith(f"{self.source_prefix}:"):
            return prefixed_id[len(self.source_prefix) + 1:]
        return prefixed_id

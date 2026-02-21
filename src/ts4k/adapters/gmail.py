"""Gmail adapter — MCP client bridge to google_workspace_mcp.

This adapter connects to the google_workspace_mcp server as an MCP
**client**, calls its Gmail tools, and parses the plain-text responses
into the normalised dicts that the ts4k pipeline expects.

Supports two connection modes:

* **stdio** — launches the upstream server as a subprocess.
* **streamable-http** — connects to an already-running HTTP server.

Usage::

    adapter = GmailAdapter(
        user_email="alice@gmail.com",
        transport="stdio",
        server_command=["python", "main.py", "--tools", "gmail", "--single-user"],
        server_cwd="H:/source/google_workspace_mcp",
    )
    async with adapter:
        msgs = await adapter.list_messages("newer_than:1d")
"""

from __future__ import annotations

import logging
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from ts4k.adapters.base import BaseAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class GmailAdapterConfig:
    """All knobs for the Gmail adapter in one place."""

    user_email: str
    """Google email passed to every upstream tool call."""

    transport: str = "stdio"
    """``'stdio'`` or ``'streamable-http'``."""

    # stdio-specific
    server_command: list[str] = field(default_factory=lambda: ["python", "main.py"])
    server_args: list[str] = field(
        default_factory=lambda: ["--tools", "gmail", "--single-user"]
    )
    server_cwd: str | None = None
    server_env: dict[str, str] | None = None

    # HTTP-specific
    server_url: str = "http://localhost:8000/mcp"


# ---------------------------------------------------------------------------
# Response parsers — turn upstream plain-text into structured dicts
# ---------------------------------------------------------------------------

# Regex for a numbered message entry in search results:
#   1. Message ID: 18d4a2b3c4e5f6a7
#      Web Link: https://...
#      Thread ID: 18d4a2b3c4e5f6a8
#      Thread Link: https://...
_SEARCH_ENTRY = re.compile(
    r"^\s*\d+\.\s+Message ID:\s*(?P<msg_id>\S+)\s*\n"
    r"\s+Web Link:\s*(?P<web_link>\S+)\s*\n"
    r"\s+Thread ID:\s*(?P<thread_id>\S+)\s*\n"
    r"\s+Thread Link:\s*(?P<thread_link>\S+)",
    re.MULTILINE,
)

# Header lines in a single-message response.
_HEADER_RE = re.compile(
    r"^(?P<key>Subject|From|Date|To|Cc|Message-ID|In-Reply-To|References):\s+(?P<value>.+)$",
    re.MULTILINE,
)

# Body delimiter.
_BODY_DELIM = "--- BODY ---"
_ATTACH_DELIM = "--- ATTACHMENTS ---"

# Thread message separator: === Message N ===
_THREAD_MSG_SEP = re.compile(r"^=== Message (\d+) ===$", re.MULTILINE)

# Pagination token.
_PAGINATION_RE = re.compile(r"page_token='([^']+)'")


def parse_search_response(text: str, prefix: str) -> list[dict]:
    """Parse the upstream ``search_gmail_messages`` plain-text response.

    Returns a list of dicts with ``id``, ``thread_id``, ``web_link``,
    ``thread_link``.  All IDs are prefixed with *prefix* (e.g. ``'g'``).
    """
    results: list[dict] = []
    for m in _SEARCH_ENTRY.finditer(text):
        msg_id = m.group("msg_id")
        thread_id = m.group("thread_id")
        results.append(
            {
                "id": f"{prefix}:{msg_id}",
                "raw_id": msg_id,
                "thread_id": f"{prefix}:{thread_id}",
                "raw_thread_id": thread_id,
                "web_link": m.group("web_link"),
                "thread_link": m.group("thread_link"),
            }
        )

    # Check for pagination token.
    pag = _PAGINATION_RE.search(text)
    if pag and results:
        results[-1]["_next_page_token"] = pag.group(1)

    return results


def parse_message_response(text: str, prefix: str, raw_id: str = "") -> dict:
    """Parse the upstream ``get_gmail_message_content`` plain-text response.

    Returns a dict with ``id``, ``from``, ``subject``, ``date``, ``body``,
    and optional ``to``, ``cc``, ``message_id``, ``attachments``.
    """
    headers: dict[str, str] = {}
    for m in _HEADER_RE.finditer(text):
        key = m.group("key")
        val = m.group("value").strip()
        # Normalise key names to lower-case + underscore.
        norm = key.lower().replace("-", "_")
        headers[norm] = val

    # Body: everything between "--- BODY ---" and "--- ATTACHMENTS ---" (or end).
    body = ""
    body_start = text.find(_BODY_DELIM)
    if body_start != -1:
        body_start += len(_BODY_DELIM)
        attach_start = text.find(_ATTACH_DELIM, body_start)
        if attach_start != -1:
            body = text[body_start:attach_start].strip()
        else:
            body = text[body_start:].strip()

    # Attachments — simple list parse.
    attachments: list[dict] = []
    attach_start = text.find(_ATTACH_DELIM)
    if attach_start != -1:
        attach_text = text[attach_start + len(_ATTACH_DELIM) :]
        # Each attachment is numbered: "1. filename.pdf (mime, size)"
        for att_match in re.finditer(
            r"(\d+)\.\s+(.+?)\s+\(([^,]+),\s*([^)]+)\)", attach_text
        ):
            attachments.append(
                {
                    "index": int(att_match.group(1)),
                    "filename": att_match.group(2).strip(),
                    "mime_type": att_match.group(3).strip(),
                    "size": att_match.group(4).strip(),
                }
            )

    result: dict[str, Any] = {
        "id": f"{prefix}:{raw_id}" if raw_id else "",
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "body": body,
    }

    if headers.get("to"):
        result["to"] = headers["to"]
    if headers.get("cc"):
        result["cc"] = headers["cc"]
    if headers.get("message_id"):
        result["message_id"] = headers["message_id"]
    if attachments:
        result["attachments"] = attachments

    return result


def parse_thread_response(text: str, prefix: str, raw_thread_id: str = "") -> dict:
    """Parse the upstream ``get_gmail_thread_content`` plain-text response.

    Returns a dict with ``thread_id``, ``subject``, ``message_count``,
    ``messages`` (list of per-message dicts).
    """
    # Thread-level headers appear before the first === Message N ===.
    thread_id_match = re.search(r"^Thread ID:\s*(\S+)", text, re.MULTILINE)
    subject_match = re.search(r"^Subject:\s*(.+)$", text, re.MULTILINE)
    count_match = re.search(r"^Messages:\s*(\d+)", text, re.MULTILINE)

    thread_id_raw = raw_thread_id or (
        thread_id_match.group(1) if thread_id_match else ""
    )
    subject = subject_match.group(1).strip() if subject_match else ""
    message_count = int(count_match.group(1)) if count_match else 0

    # Split on message separators.
    parts = _THREAD_MSG_SEP.split(text)
    # parts[0] is the header, then alternating (msg_num_str, msg_body).
    messages: list[dict] = []
    i = 1
    while i < len(parts) - 1:
        msg_num = int(parts[i])
        msg_text = parts[i + 1]
        msg = _parse_thread_message_block(msg_text, prefix, msg_num)
        messages.append(msg)
        i += 2

    return {
        "thread_id": f"{prefix}:{thread_id_raw}" if thread_id_raw else "",
        "subject": subject,
        "message_count": message_count or len(messages),
        "messages": messages,
    }


def _parse_thread_message_block(text: str, prefix: str, index: int) -> dict:
    """Parse a single message block within a thread response."""
    headers: dict[str, str] = {}
    for m in _HEADER_RE.finditer(text):
        norm = m.group("key").lower().replace("-", "_")
        headers[norm] = m.group("value").strip()

    # Body is everything after the last header line (crude but effective for
    # the upstream format, which has headers then a blank line then body).
    lines = text.strip().splitlines()
    body_lines: list[str] = []
    past_headers = False
    for line in lines:
        if past_headers:
            body_lines.append(line)
        elif line.strip() == "":
            # Blank line after header block signals start of body.
            if headers:
                past_headers = True
        elif not _HEADER_RE.match(line):
            # Non-header, non-blank — treat as body start.
            past_headers = True
            body_lines.append(line)

    result: dict[str, Any] = {
        "index": index,
        "from": headers.get("from", ""),
        "date": headers.get("date", ""),
        "body": "\n".join(body_lines).strip(),
    }

    if headers.get("subject"):
        result["subject"] = headers["subject"]
    if headers.get("message_id"):
        result["message_id"] = headers["message_id"]
    if headers.get("in_reply_to"):
        result["in_reply_to"] = headers["in_reply_to"]

    return result


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class GmailAdapter(BaseAdapter):
    """Gmail adapter that bridges to google_workspace_mcp via MCP client."""

    def __init__(self, config: GmailAdapterConfig) -> None:
        self._config = config
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    # -- BaseAdapter properties/lifecycle -----------------------------------

    @property
    def source_prefix(self) -> str:
        return "g"

    async def connect(self) -> None:
        """Open the MCP client session to the upstream server."""
        if self._session is not None:
            return  # already connected

        stack = AsyncExitStack()
        self._exit_stack = stack

        if self._config.transport == "stdio":
            params = StdioServerParameters(
                command=self._config.server_command[0],
                args=[
                    *self._config.server_command[1:],
                    *self._config.server_args,
                ],
                cwd=self._config.server_cwd,
                env=self._config.server_env,
            )
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(params)
            )
        elif self._config.transport == "streamable-http":
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamablehttp_client(self._config.server_url)
            )
        else:
            raise ValueError(
                f"Unsupported transport: {self._config.transport!r}"
            )

        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        self._session = session
        logger.info(
            "GmailAdapter connected via %s", self._config.transport
        )

    async def disconnect(self) -> None:
        """Close the MCP client session."""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            logger.info("GmailAdapter disconnected")

    # Context manager support ------------------------------------------------

    async def __aenter__(self) -> GmailAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # -- Internal helpers ----------------------------------------------------

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(
                "GmailAdapter is not connected. Call connect() or use "
                "'async with adapter:' first."
            )
        return self._session

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call an upstream MCP tool and return the text content.

        Raises ``RuntimeError`` if the tool returns an error or no text.
        """
        session = self._require_session()
        result = await session.call_tool(name, arguments)

        if result.isError:
            texts = [
                c.text for c in result.content if hasattr(c, "text")
            ]
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
        self, query: str | None = None, count: int = 20
    ) -> list[dict]:
        """Search Gmail and return a list of message-header dicts."""
        text = await self._call_tool(
            "search_gmail_messages",
            {
                "user_google_email": self._config.user_email,
                "query": query or "in:inbox",
                "page_size": count,
            },
        )
        return parse_search_response(text, self.source_prefix)

    async def read_message(self, msg_id: str) -> dict:
        """Fetch a single message by its ts4k prefixed ID (``g:XXXX``)."""
        raw_id = self._strip_prefix(msg_id)
        text = await self._call_tool(
            "get_gmail_message_content",
            {
                "user_google_email": self._config.user_email,
                "message_id": raw_id,
            },
        )
        return parse_message_response(text, self.source_prefix, raw_id)

    async def read_thread(self, thread_id: str) -> dict:
        """Fetch a full thread by its ts4k prefixed ID (``g:XXXX``)."""
        raw_id = self._strip_prefix(thread_id)
        text = await self._call_tool(
            "get_gmail_thread_content",
            {
                "user_google_email": self._config.user_email,
                "thread_id": raw_id,
            },
        )
        return parse_thread_response(text, self.source_prefix, raw_id)

    # -- Helpers -------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        """Remove the ``g:`` prefix if present, returning the raw ID."""
        if prefixed_id.startswith(f"{self.source_prefix}:"):
            return prefixed_id[len(self.source_prefix) + 1 :]
        return prefixed_id

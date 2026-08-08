"""Live parity between the HTTP and stdio WhatsApp transports (ts4k#71).

Deselected by default (``-m 'not integration'``).  Run it against a real
bridge and a real archive when changing either transport::

    TS4K_WA_STDIO_CWD=~/ai/whatsapp-mcp/whatsapp-mcp-server \
    TS4K_WA_TOKEN_FILE=~/ai/whatsapp-mcp/whatsapp-bridge/store/api_token \
    uv run pytest tests/test_whatsapp_live_parity.py -m integration -v

Both transports read the same SQLite archive through the same tool surface,
so ts4k-level output should be identical.  Where it is not, that is the
migration regressing — which is the whole question this file exists to answer.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from ts4k.adapters import wa_bridge_auth
from ts4k.adapters.whatsapp import WhatsAppAdapter, WhatsAppAdapterConfig

pytestmark = pytest.mark.integration

BRIDGE_URL = os.environ.get("TS4K_WA_BRIDGE_URL", wa_bridge_auth.DEFAULT_BRIDGE_URL)
TOKEN_FILE = os.environ.get("TS4K_WA_TOKEN_FILE", "")
STDIO_CWD = os.environ.get("TS4K_WA_STDIO_CWD", "")


def _require_bridge():
    try:
        httpx.post(BRIDGE_URL, timeout=3, trust_env=False)
    except httpx.HTTPError as exc:
        pytest.skip(f"bridge not reachable at {BRIDGE_URL}: {exc}")
    if not wa_bridge_auth.resolve_bridge_token(token_file=TOKEN_FILE):
        pytest.skip("no bridge token; set TS4K_WA_TOKEN_FILE or WHATSAPP_API_TOKEN")


def _require_stdio():
    if not STDIO_CWD or not Path(STDIO_CWD).expanduser().is_dir():
        pytest.skip("set TS4K_WA_STDIO_CWD to the whatsapp-mcp-server checkout")


def _http_adapter() -> WhatsAppAdapter:
    return WhatsAppAdapter(
        WhatsAppAdapterConfig(bridge_url=BRIDGE_URL, bridge_token_file=TOKEN_FILE),
        prefix="w",
    )


def _stdio_adapter() -> WhatsAppAdapter:
    return WhatsAppAdapter(
        WhatsAppAdapterConfig(
            transport="stdio", server_cwd=str(Path(STDIO_CWD).expanduser())
        ),
        prefix="w",
    )


async def _both(call):
    _require_bridge()
    _require_stdio()
    async with _http_adapter() as http:
        over_http = await call(http)
    async with _stdio_adapter() as stdio:
        over_stdio = await call(stdio)
    return over_http, over_stdio


async def test_http_transport_reaches_real_chats():
    _require_bridge()
    async with _http_adapter() as adapter:
        chats = await adapter.list_messages(count=5)
    assert chats, "no chats returned — the archive is empty or the call failed"
    assert all(c["id"].startswith("w:") for c in chats)


async def test_list_chats_matches_stdio():
    over_http, over_stdio = await _both(lambda a: a.list_messages(count=10))
    assert over_http == over_stdio


async def test_whatsnew_matches_stdio():
    # Fixed window: "last 24 hours" would drift between the two calls.
    since = "2026-01-01T00:00:00+00:00"
    over_http, over_stdio = await _both(lambda a: a.whatsnew(since=since))
    assert over_http == over_stdio


async def test_read_thread_matches_stdio():
    _require_bridge()
    async with _http_adapter() as adapter:
        chats = await adapter.list_messages(count=1)
    if not chats:
        pytest.skip("no chats in the archive")
    chat_id = chats[0]["id"]
    over_http, over_stdio = await _both(lambda a: a.read_thread(chat_id))
    assert over_http == over_stdio


async def test_read_message_matches_stdio():
    _require_bridge()
    async with _http_adapter() as adapter:
        msgs = await adapter.whatsnew(since="2026-01-01T00:00:00+00:00")
    if not msgs:
        pytest.skip("no recent messages in the archive")
    msg_id = msgs[0]["id"]
    over_http, over_stdio = await _both(lambda a: a.read_message(msg_id))
    assert over_http == over_stdio

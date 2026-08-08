"""Transport selection for the WhatsApp adapter (ts4k#71).

The HTTP transport talks to the Go bridge directly; ``transport=stdio`` is the
documented rollback that spawns the Python whatsapp-mcp-server.  These pin the
wiring — which transport a config selects, and that the HTTP path never
reaches for a subprocess.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from ts4k import commands
from ts4k.adapters import wa_bridge_auth, whatsapp
from ts4k.adapters.whatsapp import WhatsAppAdapter, WhatsAppAdapterConfig


# ---------------------------------------------------------------------------
# Config → adapter
# ---------------------------------------------------------------------------


def test_http_is_the_default_transport():
    adapter = commands._make_adapter("w", {"provider": "whatsapp"})
    assert adapter._config.transport == "http"
    assert adapter._config.bridge_url == wa_bridge_auth.DEFAULT_BRIDGE_URL


def test_http_does_not_require_a_local_checkout():
    """ts4k may run on a different host from the bridge."""
    adapter = commands._make_adapter("w", {"provider": "whatsapp", "mcp_cwd": "/does/not/exist"})
    assert adapter is not None
    assert adapter._config.transport == "http"


def test_http_carries_bridge_url_and_key_through():
    adapter = commands._make_adapter("w", {
        "provider": "whatsapp",
        "bridge_url": "http://10.0.0.5:18741/mcp",
        "bridge_token_file": "/keys/api_token",
    })
    assert adapter._config.bridge_url == "http://10.0.0.5:18741/mcp"
    assert adapter._config.bridge_token_file == "/keys/api_token"


def test_stdio_rollback(tmp_path):
    adapter = commands._make_adapter("w", {
        "provider": "whatsapp",
        "transport": "stdio",
        "mcp_cwd": str(tmp_path),
        "server_command": "uv run python main.py",
    })
    assert adapter._config.transport == "stdio"
    assert adapter._config.server_command == ["uv", "run", "python", "main.py"]
    assert adapter._config.server_cwd == str(tmp_path)


def test_stdio_without_a_checkout_is_unusable():
    assert commands._make_adapter("w", {
        "provider": "whatsapp", "transport": "stdio", "mcp_cwd": "/does/not/exist",
    }) is None


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, *streams):
        self.initialized = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def initialize(self):
        self.initialized = True


@pytest.fixture
def no_subprocess(monkeypatch):
    """Make any attempt to spawn the Python stdio server fail loudly."""
    def boom(*args, **kwargs):
        raise AssertionError("the HTTP transport must not spawn a subprocess")
    monkeypatch.setattr(whatsapp, "stdio_client", boom)


async def test_http_connect_never_spawns_a_subprocess(no_subprocess, monkeypatch):
    seen = {}

    @asynccontextmanager
    async def fake_streamable(url, *, httpx_client_factory):
        seen["url"] = url
        seen["client"] = httpx_client_factory()
        yield ("read", "write", lambda: None)

    monkeypatch.setattr(whatsapp, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(whatsapp, "ClientSession", _FakeSession)

    adapter = WhatsAppAdapter(WhatsAppAdapterConfig(bridge_token="k"), prefix="w")
    async with adapter:
        assert adapter._session.initialized
    assert seen["url"] == wa_bridge_auth.DEFAULT_BRIDGE_URL
    assert seen["client"].trust_env is False


async def test_http_connect_pins_localhost_to_the_ipv4_loopback(no_subprocess, monkeypatch):
    seen = {}

    @asynccontextmanager
    async def fake_streamable(url, *, httpx_client_factory):
        seen["url"] = url
        yield ("read", "write", lambda: None)

    monkeypatch.setattr(whatsapp, "streamablehttp_client", fake_streamable)
    monkeypatch.setattr(whatsapp, "ClientSession", _FakeSession)

    adapter = WhatsAppAdapter(
        WhatsAppAdapterConfig(bridge_url="http://localhost:18741/mcp", bridge_token="k"),
        prefix="w",
    )
    async with adapter:
        pass
    assert seen["url"] == "http://127.0.0.1:18741/mcp"


async def test_a_failed_connect_leaves_no_half_open_adapter(monkeypatch):
    @asynccontextmanager
    async def fake_streamable(url, *, httpx_client_factory):
        raise ConnectionRefusedError("bridge is down")
        yield  # pragma: no cover

    monkeypatch.setattr(whatsapp, "streamablehttp_client", fake_streamable)

    adapter = WhatsAppAdapter(WhatsAppAdapterConfig(bridge_token="k"), prefix="w")
    with pytest.raises(ConnectionRefusedError):
        await adapter.connect()
    assert adapter._session is None
    assert adapter._exit_stack is None


# ---------------------------------------------------------------------------
# Connect failures
# ---------------------------------------------------------------------------
#
# The HTTP transport runs inside an anyio task group: a failed request cancels
# initialize(), and the error explaining why only surfaces when the group
# unwinds during teardown.  Report either half naively and the operator is told
# "Cancelled via cancel scope" or "unhandled errors in a TaskGroup", which name
# nothing.


def _adapter() -> WhatsAppAdapter:
    return WhatsAppAdapter(WhatsAppAdapterConfig(bridge_token="k"), prefix="w")


def test_a_401_names_the_bridge_key():
    unauthorized = httpx.HTTPStatusError(
        "Client error '401 Unauthorized'",
        request=httpx.Request("POST", wa_bridge_auth.DEFAULT_BRIDGE_URL),
        response=httpx.Response(401),
    )
    error = _adapter()._connect_error(
        asyncio.CancelledError("Cancelled via cancel scope"),
        ExceptionGroup("unhandled errors in a TaskGroup", [unauthorized]),
    )
    assert isinstance(error, RuntimeError)
    assert "bridge_token" in str(error)
    assert "401" in str(error)


def test_the_real_cause_beats_the_cancellation():
    refused = httpx.ConnectError("All connection attempts failed")
    error = _adapter()._connect_error(
        asyncio.CancelledError(),
        ExceptionGroup("unhandled errors in a TaskGroup", [refused]),
    )
    assert error is refused


def test_nested_groups_are_flattened():
    refused = httpx.ConnectError("All connection attempts failed")
    error = _adapter()._connect_error(
        ExceptionGroup("outer", [ExceptionGroup("inner", [refused])]), None
    )
    assert error is refused


def test_a_plain_error_is_left_alone():
    boom = FileNotFoundError("uv")
    assert _adapter()._connect_error(boom, None) is boom


def test_a_genuine_cancellation_stays_a_cancellation():
    """A caller-side timeout must not be relabelled as a bridge failure."""
    cancelled = asyncio.CancelledError()
    assert _adapter()._connect_error(cancelled, None) is cancelled


# ---------------------------------------------------------------------------
# The trifecta boundary (whatsapp-mcp#46)
# ---------------------------------------------------------------------------


def test_the_adapter_has_no_send_path():
    """Read + tool-use + send is the trifecta; the third leg stays absent.

    The bridge's MCP surface excludes the send tools and enforces that on its
    own side.  This asserts ts4k does not reintroduce them from here.
    """
    forbidden = ("send_message", "send_file", "send_audio_message", "send")
    source = (whatsapp.__file__ and open(whatsapp.__file__).read()) or ""
    for name in forbidden:
        assert not hasattr(WhatsAppAdapter, name)
        assert f'"{name}"' not in source


# ---------------------------------------------------------------------------
# The key stays out of the terminal
# ---------------------------------------------------------------------------


def test_src_list_redacts_an_inline_bridge_token(ts4k_config, capsys):
    """`src list` runs constantly in agent context — the HMAC key can't ride along.

    Anyone who can read it can read the whole archive wherever the bridge is
    reachable.  The pointer form stays visible: it is a path, not a secret.
    """
    import argparse

    from ts4k import cli
    from ts4k.state import sources

    sources.add(
        "w",
        provider="whatsapp",
        bridge_token="s3cr3t-hmac-key",
        bridge_token_file="/keys/api_token",
        bridge_url="http://127.0.0.1:18741/mcp",
    )
    cli._cmd_sources(argparse.Namespace(
        action="list", prefix=None, provider=None, params=None,
    ))

    out = capsys.readouterr().out
    assert "s3cr3t-hmac-key" not in out
    assert "bridge_token: <redacted>" in out
    assert "/keys/api_token" in out
    assert "http://127.0.0.1:18741/mcp" in out


def test_src_add_redacts_an_inline_bridge_token(ts4k_config, capsys):
    """`src add` echoes the saved entry — redacting only `list` moves the leak.

    This is the branch that runs at the moment the key is first typed.
    """
    import argparse

    from ts4k import cli

    cli._cmd_sources(argparse.Namespace(
        action="add", prefix="w", provider="whatsapp",
        params=["bridge_token=s3cr3t-hmac-key", "bridge_token_file=/keys/api_token"],
    ))

    out = capsys.readouterr().out
    assert "s3cr3t-hmac-key" not in out
    assert "bridge_token: <redacted>" in out
    assert "/keys/api_token" in out

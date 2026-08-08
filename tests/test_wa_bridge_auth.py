"""Tests for the WhatsApp bridge's challenge-response auth (whatsapp-mcp#58).

The exchange under test: an unauthenticated request is answered 401 with a
single-use nonce, and the client re-sends carrying an HMAC over what it
actually sent.  The shared secret is the HMAC key and never crosses the wire.

Most of these assert properties an implementation could plausibly lose while
still appearing to work against a friendly server — the retry landing on a
fresh socket, exactly one retry, no replayable credential — so they run
against a real HTTP server rather than a mock transport.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ts4k.adapters import wa_bridge_auth as auth

TOKEN = "s3cr3t-hmac-key"
_NONCE = re.compile(r'nonce="([^"]*)"')
_MAC = re.compile(r'mac="([^"]*)"')


# ---------------------------------------------------------------------------
# A bridge-shaped test server
# ---------------------------------------------------------------------------


class _Record:
    def __init__(self, method, target, body, headers, client_port):
        self.method = method
        self.target = target
        self.body = body
        self.headers = headers
        self.client_port = client_port


class _BridgeServer:
    """Minimal stand-in for the Go bridge's auth middleware.

    Recomputes the MAC independently of the client under test, so a client
    that signs the wrong canonical string fails here exactly as it would in
    production.
    """

    def __init__(self, *, token=TOKEN, always_401=False, offer_challenge=True,
                 challenge_header=None):
        self.token = token
        self.always_401 = always_401
        self.offer_challenge = offer_challenge
        self.challenge_header = challenge_header
        self.requests: list[_Record] = []
        self._nonce_seq = 0

        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive, so connection reuse is visible

            def log_message(self, *args):  # keep pytest output clean
                pass

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                server.requests.append(_Record(
                    self.command, self.path, body, dict(self.headers),
                    self.connection.getpeername()[1],
                ))
                authorized = server._authorized(
                    self.command, self.path, body,
                    self.headers.get("Authorization", ""),
                )
                if server.always_401 or not authorized:
                    return self._challenge()
                payload = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _challenge(self):
                self.send_response(401)
                if server.challenge_header is not None:
                    self.send_header("WWW-Authenticate", server.challenge_header)
                elif server.offer_challenge:
                    self.send_header(
                        "WWW-Authenticate",
                        f'HMAC-SHA256 nonce="{server._issue_nonce()}"',
                    )
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = do_DELETE = _handle

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def _issue_nonce(self) -> str:
        self._nonce_seq += 1
        return f"nonce-{self._nonce_seq:064d}"

    def _authorized(self, method: str, target: str, body: bytes, header: str) -> bool:
        if not header.startswith("HMAC-SHA256 "):
            return False
        nonce, mac = _NONCE.search(header), _MAC.search(header)
        if not nonce or not mac:
            return False
        expected = hmac.new(
            self.token.encode(),
            f"{method}\n{target}\n{nonce.group(1)}\n{hashlib.sha256(body).hexdigest()}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, mac.group(1))

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _client(token: str | None = TOKEN):
    return auth.bridge_client_factory(token)()


# ---------------------------------------------------------------------------
# Canonical string and signing
# ---------------------------------------------------------------------------


def test_canonical_string_is_the_documented_four_line_form():
    assert auth.canonical_string("POST", "/mcp?x=1", "abc", b"hi") == (
        "POST\n/mcp?x=1\nabc\n" + hashlib.sha256(b"hi").hexdigest()
    )


def test_empty_body_hashes_the_empty_string_not_a_literal():
    # sha256("") — not sha256("null"), not sha256("{}").
    assert auth.canonical_string("GET", "/mcp", "n", b"").endswith(
        hashlib.sha256(b"").hexdigest()
    )


def test_sign_matches_hmac_of_the_canonical_string():
    expected = hmac.new(
        TOKEN.encode(),
        auth.canonical_string("POST", "/mcp", "n", b"{}").encode(),
        hashlib.sha256,
    ).hexdigest()
    assert auth.sign(TOKEN, "POST", "/mcp", "n", b"{}") == expected


# ---------------------------------------------------------------------------
# Challenge parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("header,expected", [
    ('HMAC-SHA256 nonce="abc"', "abc"),
    ('hmac-sha256 nonce="abc"', "abc"),       # scheme match is case-insensitive
    ('Bearer realm="x"', ""),                  # not our scheme
    ("HMAC-SHA256", ""),                       # scheme but no nonce
])
def test_challenge_nonce(header, expected):
    assert auth.challenge_nonce({"WWW-Authenticate": header}) == expected


def test_challenge_nonce_without_a_header():
    assert auth.challenge_nonce({}) == ""


# ---------------------------------------------------------------------------
# The exchange
# ---------------------------------------------------------------------------


async def test_signed_retry_succeeds():
    with _BridgeServer() as server:
        async with _client() as client:
            resp = await client.post(f"{server.url}/mcp", content=b'{"a":1}')
        assert resp.status_code == 200
        assert len(server.requests) == 2
        assert "Authorization" not in server.requests[0].headers
        assert server.requests[1].headers["Authorization"].startswith("HMAC-SHA256 ")


async def test_retry_travels_over_a_fresh_connection():
    """The signed answer must not reuse the socket the challenge came back on.

    A squatter can accept the first connection, release only its *listening*
    socket so the real bridge can bind, relay the challenge, and then receive
    the signed answer over the still-open socket.  Reconnecting puts the
    credential in front of whichever process owns the port now.
    """
    with _BridgeServer() as server:
        async with _client() as client:
            await client.post(f"{server.url}/mcp", content=b"{}")
        assert len(server.requests) == 2
        assert server.requests[0].client_port != server.requests[1].client_port


async def test_a_second_401_is_reported_not_resigned():
    """One retry, exactly.  A bridge holding a different key must not loop."""
    with _BridgeServer(always_401=True) as server:
        async with _client() as client:
            resp = await client.post(f"{server.url}/mcp", content=b"{}")
        assert resp.status_code == 401
        assert len(server.requests) == 2


async def test_no_token_means_no_retry():
    with _BridgeServer() as server:
        async with _client(token=None) as client:
            resp = await client.post(f"{server.url}/mcp", content=b"{}")
        assert resp.status_code == 401
        assert len(server.requests) == 1


async def test_a_401_without_our_scheme_is_not_answered():
    with _BridgeServer(challenge_header='Basic realm="bridge"') as server:
        async with _client() as client:
            resp = await client.post(f"{server.url}/mcp", content=b"{}")
        assert resp.status_code == 401
        assert len(server.requests) == 1


async def test_a_401_with_no_challenge_at_all_is_not_answered():
    with _BridgeServer(offer_challenge=False) as server:
        async with _client() as client:
            resp = await client.post(f"{server.url}/mcp", content=b"{}")
        assert resp.status_code == 401
        assert len(server.requests) == 1


async def test_no_replayable_credential_is_ever_sent():
    """The token is an HMAC key.  It must not appear on the wire, in any form."""
    with _BridgeServer() as server:
        async with _client() as client:
            await client.post(f"{server.url}/mcp", content=b"{}")
        for record in server.requests:
            blob = json.dumps(record.headers)
            assert TOKEN not in blob
            assert "Bearer" not in blob


async def test_signature_covers_the_query_string_as_sent():
    """request_target is path + query — the bridge signs r.URL.RequestURI()."""
    with _BridgeServer() as server:
        async with _client() as client:
            resp = await client.post(f"{server.url}/mcp?session=7&x=y", content=b"{}")
        # 200 means the server recomputed the same MAC over "/mcp?session=7&x=y".
        assert resp.status_code == 200
        assert server.requests[1].target == "/mcp?session=7&x=y"


async def test_signature_covers_a_bodyless_get():
    with _BridgeServer() as server:
        async with _client() as client:
            resp = await client.get(f"{server.url}/mcp")
        assert resp.status_code == 200
        assert server.requests[1].body == b""


async def test_retry_resends_the_same_body():
    body = b'{"jsonrpc":"2.0","method":"tools/list"}'
    with _BridgeServer() as server:
        async with _client() as client:
            resp = await client.post(f"{server.url}/mcp", content=body)
        assert resp.status_code == 200
        assert server.requests[0].body == body
        assert server.requests[1].body == body


async def test_each_call_is_challenged_separately():
    """Nonces are single-use, so there is nothing to carry between calls."""
    with _BridgeServer() as server:
        async with _client() as client:
            assert (await client.post(f"{server.url}/mcp", content=b"{}")).status_code == 200
            assert (await client.post(f"{server.url}/mcp", content=b"{}")).status_code == 200
        assert len(server.requests) == 4
        nonces = [_NONCE.search(r.headers["Authorization"]).group(1)
                  for r in server.requests if "Authorization" in r.headers]
        assert len(set(nonces)) == 2


# ---------------------------------------------------------------------------
# Client hardening
# ---------------------------------------------------------------------------


def test_client_does_not_trust_the_environment():
    """Proxy env vars must not be able to route bridge traffic elsewhere."""
    assert _client().trust_env is False


def test_client_does_not_follow_redirects():
    assert _client().follow_redirects is False


async def test_env_proxy_is_ignored(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # a closed port
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    with _BridgeServer() as server:
        async with _client() as client:
            resp = await client.post(f"{server.url}/mcp", content=b"{}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# URL pinning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("given,expected", [
    ("http://localhost:18741/mcp", "http://127.0.0.1:18741/mcp"),
    ("http://LOCALHOST:18741/mcp", "http://127.0.0.1:18741/mcp"),
    ("http://localhost/mcp", "http://127.0.0.1/mcp"),
    ("http://127.0.0.1:18741/mcp", "http://127.0.0.1:18741/mcp"),
    ("http://bridge.internal:18741/mcp", "http://bridge.internal:18741/mcp"),
    ("http://localhost.example.com/mcp", "http://localhost.example.com/mcp"),
])
def test_pin_loopback(given, expected):
    assert auth.pin_loopback(given) == expected


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


def test_token_from_config_wins(monkeypatch):
    monkeypatch.setenv("WHATSAPP_API_TOKEN", "from-env")
    assert auth.resolve_bridge_token(token="from-config") == "from-config"


def test_token_from_config_file(monkeypatch, tmp_path):
    monkeypatch.delenv("WHATSAPP_API_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_API_TOKEN_FILE", raising=False)
    path = tmp_path / "api_token"
    path.write_text("  from-file\n")
    assert auth.resolve_bridge_token(token_file=str(path)) == "from-file"


def test_token_from_env(monkeypatch):
    monkeypatch.setenv("WHATSAPP_API_TOKEN", "from-env")
    assert auth.resolve_bridge_token() == "from-env"


def test_token_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("WHATSAPP_API_TOKEN", raising=False)
    path = tmp_path / "api_token"
    path.write_text("from-env-file")
    monkeypatch.setenv("WHATSAPP_API_TOKEN_FILE", str(path))
    assert auth.resolve_bridge_token() == "from-env-file"


def test_token_missing_is_empty_not_an_exception(monkeypatch, tmp_path):
    monkeypatch.delenv("WHATSAPP_API_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_API_TOKEN_FILE", raising=False)
    assert auth.resolve_bridge_token(token_file=str(tmp_path / "nope")) == ""


def test_an_unreadable_file_does_not_shadow_the_environment(monkeypatch, tmp_path):
    """A config copied to another host keeps a path that resolves to nothing.

    If that dead path short-circuits, the documented env fallback is
    unreachable exactly when it is the thing that would work.
    """
    monkeypatch.setenv("WHATSAPP_API_TOKEN", "from-env")
    assert auth.resolve_bridge_token(token_file=str(tmp_path / "nope")) == "from-env"


def test_an_empty_file_does_not_shadow_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("WHATSAPP_API_TOKEN", "from-env")
    path = tmp_path / "api_token"
    path.write_text("   \n")
    assert auth.resolve_bridge_token(token_file=str(path)) == "from-env"


def test_a_readable_file_still_beats_the_environment(monkeypatch, tmp_path):
    """Precedence is unchanged when the file actually resolves."""
    monkeypatch.setenv("WHATSAPP_API_TOKEN", "from-env")
    path = tmp_path / "api_token"
    path.write_text("from-file")
    assert auth.resolve_bridge_token(token_file=str(path)) == "from-file"


def test_config_file_path_is_expanded(monkeypatch, tmp_path):
    monkeypatch.delenv("WHATSAPP_API_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_API_TOKEN_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "api_token").write_text("tilde-token")
    assert auth.resolve_bridge_token(token_file="~/api_token") == "tilde-token"

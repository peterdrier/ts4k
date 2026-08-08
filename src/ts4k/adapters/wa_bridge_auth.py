"""Challenge-response auth for the WhatsApp bridge's HTTP MCP endpoint.

The Go bridge (whatsapp-mcp) serves MCP over streamable HTTP behind the
challenge-response middleware from whatsapp-mcp#58.  The shared secret is an
HMAC key, not a bearer token, and it never crosses the wire:

1. The request goes out unauthenticated.
2. The bridge answers ``401`` with a single-use nonce in ``WWW-Authenticate``.
3. The client re-sends carrying an HMAC over the method, the request target,
   that nonce, and a hash of the body — **over a fresh connection**.
4. Exactly one retry.  A second 401 is reported, never re-signed.

This is a port of ``_bridge_challenge`` / ``_bridge_auth_headers`` /
``_bridge_json_request`` in whatsapp-mcp's ``whatsapp-mcp-server/whatsapp.py``.
The canonical string has to agree with ``requestMAC`` in
``whatsapp-bridge/auth.go`` byte for byte, which is why it is built from what
was *actually sent* rather than from what a caller meant to send.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# The bridge's default listen address.  Configurable — ts4k may run on a
# different host — but the literal 127.0.0.1 rather than "localhost": see
# pin_loopback.
DEFAULT_BRIDGE_URL = "http://127.0.0.1:18741/mcp"

# Deliberately not "Bearer".  The token is an HMAC key here, so a client that
# still sends one as a credential must fail rather than appear to work.
AUTH_SCHEME = "HMAC-SHA256"

_NONCE_PATTERN = re.compile(r'nonce="([^"]*)"')


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def pin_loopback(url: str) -> str:
    """Rewrite a ``localhost`` bridge URL to the literal ``127.0.0.1``.

    The bridge binds the IPv4 loopback only.  On a dual-stack host
    "localhost" can resolve to ``::1`` first, and another local account can
    bind ``[::1]:18741`` *alongside* the live bridge — the one position from
    which a signed request can still be relayed.  Pinning the literal keeps
    client and bridge in the same address family.  Non-loopback hosts are
    left alone; ts4k is allowed to run elsewhere.
    """
    parsed = httpx.URL(url)
    if parsed.host.lower() != "localhost":
        return url
    logger.warning(
        "Bridge URL uses 'localhost'; pinning to 127.0.0.1 so the connection "
        "cannot land on an IPv6 listener alongside the bridge"
    )
    return str(parsed.copy_with(host="127.0.0.1"))


def resolve_bridge_token(token: str | None = None, token_file: str | None = None) -> str:
    """Resolve the bridge's shared secret, or ``""`` if it is not configured.

    Order: explicit config value, explicit config file, then the same two
    environment variables ``resolveAPIToken`` in ``whatsapp-bridge/auth.go``
    reads.  No path is guessed from the stdio server's location — ts4k may run
    on a different host from the bridge, where such a guess would silently
    resolve to the wrong file or to nothing.

    An unresolved token is not an error here: the request then goes out
    unauthenticated and the bridge answers 401, which is a debuggable failure
    rather than a silent one.
    """
    if token and token.strip():
        return token.strip()
    if token_file and token_file.strip():
        return _read_token_file(token_file.strip())
    env_token = os.environ.get("WHATSAPP_API_TOKEN", "").strip()
    if env_token:
        return env_token
    env_file = os.environ.get("WHATSAPP_API_TOKEN_FILE", "").strip()
    if env_file:
        return _read_token_file(env_file)
    return ""


def _read_token_file(path: str) -> str:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("Bridge token file %s is unreadable", path)
        return ""


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def canonical_string(method: str, request_target: str, nonce: str, body: bytes) -> str:
    """Build the string the bridge signs: method, target, nonce, body hash.

    ``request_target`` is path + query exactly as sent (the bridge uses
    ``r.URL.RequestURI()``).  ``body`` is the bytes actually sent — an empty
    body hashes the empty string, not ``"null"`` or ``"{}"``.
    """
    return f"{method}\n{request_target}\n{nonce}\n{hashlib.sha256(body).hexdigest()}"


def sign(token: str, method: str, request_target: str, nonce: str, body: bytes) -> str:
    """HMAC-SHA256 the canonical string with the shared secret as the key."""
    return hmac.new(
        token.encode(),
        canonical_string(method, request_target, nonce, body).encode(),
        hashlib.sha256,
    ).hexdigest()


def challenge_nonce(headers) -> str:
    """Read the nonce out of a 401's ``WWW-Authenticate`` header, or ``""``.

    An empty answer covers both a listener that does not speak this scheme and
    a genuine 401 with nothing to sign; either way there is no second attempt
    to make, and the caller reports the 401 as it stands.
    """
    scheme, _, params = headers.get("WWW-Authenticate", "").partition(" ")
    if scheme.lower() != AUTH_SCHEME.lower():
        return ""
    match = _NONCE_PATTERN.search(params)
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class BridgeHmacTransport(httpx.AsyncBaseTransport):
    """An httpx transport that answers the bridge's challenge, once.

    This lives at the transport layer rather than in an ``httpx.Auth`` flow so
    that the connection carrying the challenge is provably gone before the
    signed answer goes out — see ``_inner`` below.
    """

    def __init__(self, token: str, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token or ""
        # max_keepalive_connections=0 is load-bearing, not tuning.  It means a
        # connection is closed when its response is released rather than
        # returned to the idle pool, so the signed retry *cannot* reuse the
        # socket the challenge came back on.  That matters: during a restart
        # window a squatter can accept the client's first connection, close
        # only its *listening* socket so the real bridge can bind, fetch a
        # genuine nonce from that bridge and hand it back over the still-open
        # socket.  Answering over the same connection would deliver a valid
        # signed request straight to the squatter.  Reconnecting means the
        # credential lands on whichever process owns the port now.
        #
        # A `Connection: close` request header would not do this — it is a
        # request to the *peer*, and the peer here is the attacker.  The pool
        # has to be emptied on this side.
        # tests/test_wa_bridge_auth.py::test_retry_travels_over_a_fresh_connection
        # asserts the property directly.
        self._inner = inner or httpx.AsyncHTTPTransport(
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if response.status_code != 401:
            return response

        nonce = challenge_nonce(response.headers)
        if not nonce or not self._token:
            return response

        # Sign what was actually sent: raw_path is the target as it goes on the
        # wire, and request.content is the body httpx serialized.  If those ever
        # disagreed with what the caller meant, the bridge would answer 401
        # rather than accept a payload nobody signed.
        target = request.url.raw_path.decode("ascii")
        body = request.content
        mac = sign(self._token, request.method, target, nonce, body)

        # Drain and release the challenge before the credential goes anywhere,
        # never after: releasing is what closes that connection.
        await response.aread()
        await response.aclose()

        signed = httpx.Request(
            request.method,
            request.url,
            headers=request.headers,
            content=body,
            extensions=request.extensions,
        )
        signed.headers["Authorization"] = f'{AUTH_SCHEME} nonce="{nonce}", mac="{mac}"'
        # Exactly one retry.  A second 401 is returned as it stands — otherwise
        # a bridge holding a different key becomes an infinite loop.  Re-sending
        # is side-effect free because the middleware rejects before any handler
        # runs.
        return await self._inner.handle_async_request(signed)

    async def aclose(self) -> None:
        await self._inner.aclose()


def bridge_client_factory(token: str | None):
    """Return an ``McpHttpClientFactory`` bound to this bridge's secret.

    Handed to ``streamablehttp_client``, which owns the client's lifecycle.
    """

    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout if timeout is not None else httpx.Timeout(30.0, read=300.0),
            auth=auth,
            transport=BridgeHmacTransport(token or ""),
            # The bridge is loopback-only by default, so nothing in the
            # environment should be able to redirect this traffic: trust_env
            # disables proxy env vars (HTTP_PROXY/HTTPS_PROXY/ALL_PROXY) and
            # the SSL_CERT_FILE class of override in one place.  httpx does not
            # apply ~/.netrc automatically the way requests does — that hazard,
            # documented in whatsapp.py, does not reach us — but leaving
            # trust_env on would still hand the environment a say.
            trust_env=False,
            # MCP's default client follows redirects.  A redirect here could
            # only move bridge traffic somewhere it should not go; there is
            # nothing legitimate for it to follow.
            follow_redirects=False,
        )

    return factory

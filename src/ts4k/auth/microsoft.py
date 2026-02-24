"""Microsoft OAuth credential management for ts4k.

Handles OAuth 2.0 via MSAL device code flow for Microsoft Graph API.
Credentials are cached per-client under ~/.config/ts4k/microsoft/<client_id>/.

Device code flow is used (no browser redirect needed) — suitable for
headless servers, WSL, and SSH sessions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import httpx
import msal

logger = logging.getLogger(__name__)

GRAPH_MAIL_READ_SCOPES = ["https://graph.microsoft.com/Mail.Read"]

_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "ts4k"


def _cache_path(client_id: str, config_dir: Path) -> Path:
    """Return the token cache path for a given client_id."""
    return config_dir / "microsoft" / client_id / "token_cache.json"


def get_credentials(
    client_id: str,
    tenant_id: str = "common",
    scopes: list[str] | None = None,
    config_dir: Path | None = None,
) -> dict:
    """Load or create OAuth credentials for the given app registration.

    Tries ``acquire_token_silent()`` first.  Falls back to device code flow
    (prints instructions to stderr so they don't pollute stdout/pipe output).

    Returns a dict with at least ``access_token``.

    Raises ``ValueError`` if *client_id* is empty.
    Raises ``RuntimeError`` if the auth flow fails.
    """
    if not client_id:
        raise ValueError("client_id is required for Microsoft auth")

    scopes = scopes or GRAPH_MAIL_READ_SCOPES
    config_dir = config_dir or _DEFAULT_CONFIG_DIR

    # Load or create token cache.
    cache_file = _cache_path(client_id, config_dir)
    cache = msal.SerializableTokenCache()
    if cache_file.is_file():
        cache.deserialize(cache_file.read_text(encoding="utf-8"))
        logger.debug("Loaded token cache from %s", cache_file)

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.PublicClientApplication(
        client_id, authority=authority, token_cache=cache
    )

    # Try silent acquisition first.
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
        if result and "access_token" in result:
            _persist_cache(cache, cache_file)
            logger.debug("Token acquired silently for %s", accounts[0].get("username", "?"))
            return result

    # Fall back to device code flow.
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError(
            f"Device flow initiation failed: {flow.get('error_description', flow)}"
        )

    # Print device code instructions to stderr (not stdout).
    print(flow["message"], file=sys.stderr)

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(
            f"Authentication failed: {result.get('error_description', result)}"
        )

    _persist_cache(cache, cache_file)
    logger.info("Authenticated via device code flow, token cached at %s", cache_file)
    return result


def _persist_cache(cache: msal.SerializableTokenCache, cache_file: Path) -> None:
    """Write the token cache to disk if it has changed."""
    if cache.has_state_changed:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(cache.serialize(), encoding="utf-8")


def build_graph_client(
    client_id: str,
    tenant_id: str = "common",
    config_dir: Path | None = None,
    scopes: list[str] | None = None,
) -> httpx.AsyncClient:
    """Build an authenticated ``httpx.AsyncClient`` for Microsoft Graph.

    The client has ``base_url`` set to ``https://graph.microsoft.com/v1.0``
    and a default ``Authorization: Bearer <token>`` header.

    This is a synchronous call (token acquisition may block for device code
    flow).  The returned client is async and must be used with ``await``.
    """
    creds = get_credentials(
        client_id, tenant_id=tenant_id, scopes=scopes, config_dir=config_dir
    )
    token = creds["access_token"]

    return httpx.AsyncClient(
        base_url="https://graph.microsoft.com/v1.0",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )

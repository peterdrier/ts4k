"""Google OAuth credential management for ts4k.

Handles OAuth 2.0 credential flow for Google APIs (Gmail initially).
Credentials are stored per-account under ~/.config/ts4k/google/<email>/.

Resolution chain for client_secret.json (first found wins):
1. ~/.config/ts4k/google/<email>/client_secret.json  (per-account)
2. ~/.config/ts4k/google/client_secret.json           (shared)
"""

from __future__ import annotations

import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

GMAIL_READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def _default_config_dir() -> Path:
    """Resolve auth config dir: env var → global default.

    Auth intentionally ignores ``.ts4k/`` in cwd — credentials stay global
    unless ``TS4K_CONFIG_DIR`` is explicitly set (e.g. Docker).
    """
    import os

    env = os.environ.get("TS4K_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "ts4k"


def _resolve_client_secret(email: str, config_dir: Path) -> Path | None:
    """Find client_secret.json using the resolution chain."""
    candidates = [
        config_dir / "google" / email / "client_secret.json",
        config_dir / "google" / "client_secret.json",
    ]
    for path in candidates:
        if path.is_file():
            logger.debug("Found client_secret at %s", path)
            return path
    return None


def _token_path(email: str, config_dir: Path) -> Path:
    """Return the token storage path for a given email."""
    return config_dir / "google" / email / "token.json"


def get_credentials(
    email: str,
    scopes: list[str] | None = None,
    config_dir: Path | None = None,
) -> Credentials:
    """Load or create OAuth credentials for *email*.

    If a valid token exists, it's reused (refreshing if expired).
    Otherwise, the OAuth browser flow is triggered.

    Raises ``FileNotFoundError`` if no client_secret.json is found.
    Raises ``RuntimeError`` if the auth flow fails.
    """
    scopes = scopes or GMAIL_READONLY_SCOPES
    config_dir = config_dir or _default_config_dir()

    token_file = _token_path(email, config_dir)
    creds: Credentials | None = None

    # Try loading existing token.
    if token_file.is_file():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        logger.debug("Loaded existing token from %s", token_file)

    # Refresh or re-auth.
    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json())
            logger.info("Refreshed token for %s", email)
            return creds
        except Exception as exc:
            logger.warning("Token refresh failed for %s: %s", email, exc)
            # Fall through to full re-auth.

    # Full OAuth flow.
    secret_path = _resolve_client_secret(email, config_dir)
    if secret_path is None:
        raise FileNotFoundError(
            f"No client_secret.json found for {email}. "
            f"Place it at {config_dir / 'google' / email / 'client_secret.json'} "
            f"or {config_dir / 'google' / 'client_secret.json'}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), scopes)
    try:
        creds = flow.run_local_server(port=0)
    except Exception:
        # Fallback for headless environments.
        logger.info("Browser flow failed, trying console flow")
        creds = flow.run_console()

    if creds is None:
        raise RuntimeError(f"OAuth flow returned no credentials for {email}")

    # Persist token.
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    logger.info("Saved new token for %s at %s", email, token_file)
    return creds


def build_gmail_service(
    email: str,
    config_dir: Path | None = None,
    scopes: list[str] | None = None,
):
    """Build and return a Gmail API service resource.

    Returns a ``googleapiclient.discovery.Resource`` for the Gmail API v1.
    """
    creds = get_credentials(email, scopes=scopes, config_dir=config_dir)
    return build("gmail", "v1", credentials=creds)

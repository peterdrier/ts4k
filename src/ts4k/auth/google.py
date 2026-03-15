"""Google OAuth credential management for ts4k.

Handles OAuth 2.0 credential flow for Google APIs (Gmail initially).
Credentials are stored per-account under ~/.config/ts4k/google/<email>/.

Resolution chain for client_secret.json (first found wins):
1. ~/.config/ts4k/google/<email>/client_secret.json  (per-account)
2. ~/.config/ts4k/google/client_secret.json           (shared)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ts4k.auth.health import TokenHealth

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


def validate_token(
    email: str,
    config_dir: Path | None = None,
    required_scopes: list[str] | None = None,
) -> TokenHealth:
    """Check token health without triggering interactive auth flows.

    When *required_scopes* is provided, also verifies that the token's
    granted scopes are a superset of the required scopes.  A token that
    is valid but under-scoped returns ``"auth"`` (needs re-auth to add
    the missing scopes).

    Returns a TokenHealth with status:
      - "ok": token is valid (possibly after a silent refresh)
      - "auth": token missing, expired, or refresh failed — needs re-auth
      - "error": unexpected error during validation
    """
    config_dir = config_dir or _default_config_dir()
    token_file = _token_path(email, config_dir)

    if not token_file.is_file():
        return TokenHealth(
            status="auth",
            expiry=None,
            scopes=[],
            detail="no token file",
        )

    # Load token without triggering browser flow
    import json
    try:
        token_data = json.loads(token_file.read_text())
        granted = token_data.get("scopes", [])
        creds = Credentials.from_authorized_user_file(str(token_file), granted)
    except Exception as exc:
        return TokenHealth(
            status="error",
            expiry=None,
            scopes=[],
            detail=f"token load failed: {exc}",
        )

    # Check scope coverage before checking validity
    if required_scopes and not set(required_scopes).issubset(set(granted)):
        missing = set(required_scopes) - set(granted)
        short = [s.rsplit("/", 1)[-1] for s in missing]
        return TokenHealth(
            status="auth",
            expiry=creds.expiry,
            scopes=list(granted),
            detail=f"missing scopes: {', '.join(short)}",
        )

    if creds.valid:
        return TokenHealth(
            status="ok",
            expiry=creds.expiry,
            scopes=list(creds.scopes or []),
            detail="valid",
        )

    # Try silent refresh
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Persist refreshed token
            token_file.write_text(creds.to_json())
            return TokenHealth(
                status="ok",
                expiry=creds.expiry,
                scopes=list(creds.scopes or []),
                detail="refreshed",
            )
        except Exception as exc:
            return TokenHealth(
                status="auth",
                expiry=None,
                scopes=list(creds.scopes or []),
                detail=f"refresh failed: {exc}",
            )

    return TokenHealth(
        status="auth",
        expiry=None,
        scopes=list(creds.scopes or []),
        detail="token expired, no refresh token",
    )


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
        # Check if stored token has sufficient scopes before loading.
        import json
        try:
            token_data = json.loads(token_file.read_text())
            granted = set(token_data.get("scopes", []))
            needed = set(scopes)
            if needed and not needed.issubset(granted):
                missing = needed - granted
                logger.warning(
                    "Token for %s has scopes %s but needs %s — re-authenticating",
                    email, granted, missing,
                )
                token_file.unlink()
                # Fall through to full OAuth flow below.
            else:
                creds = Credentials.from_authorized_user_file(str(token_file), scopes)
                logger.debug("Loaded existing token from %s", token_file)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read token file %s: %s", token_file, exc)

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
            f"or {config_dir / 'google' / 'client_secret.json'}. "
            f"Setup guide: https://github.com/peterdrier/ts4k/blob/main/docs/setup-gmail.md"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), scopes)
    try:
        # Desktop with browser — just works
        creds = flow.run_local_server(port=8085, open_browser=True)
    except Exception:
        # Headless fallback: user opens URL in remote browser, pastes redirect URL back
        if not sys.stdin.isatty():
            raise RuntimeError(
                f"Gmail auth needs browser interaction for {email} "
                f"— run 'ts4k auth g' in a terminal."
            )
        logger.info("Local server flow failed, falling back to URL paste flow")
        flow.redirect_uri = "http://localhost:8085/"
        auth_url, _ = flow.authorization_url(prompt="consent")
        print("\nNo browser available. Open this URL in any browser:\n")
        print(f"  {auth_url}\n")
        print("After signing in, your browser will try to redirect to a page that won't load.")
        print("Copy the FULL URL from your browser's address bar and paste it here:\n")
        redirect_url = input("> ").strip()
        # Allow http://localhost — safe for local OAuth, required by oauthlib
        import os
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        flow.fetch_token(authorization_response=redirect_url)
        del os.environ["OAUTHLIB_INSECURE_TRANSPORT"]
        creds = flow.credentials

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


def build_calendar_service(
    email: str,
    config_dir: Path | None = None,
    scopes: list[str] | None = None,
):
    """Build a Google Calendar API v3 service client.

    Reuses the same credential flow as Gmail — tokens are shared per email.
    If the account already has a Gmail token, adding calendar scopes triggers
    a one-time re-auth.
    """
    creds = get_credentials(email, scopes or [], config_dir)
    return build("calendar", "v3", credentials=creds)

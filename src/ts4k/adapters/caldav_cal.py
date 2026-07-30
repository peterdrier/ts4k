"""CalDAV calendar adapter — generic RFC 4791, Apple/iCloud preset.

Wraps the synchronous ``caldav`` library in ``asyncio.to_thread`` (same
wrap-a-sync-client pattern as the Google adapter).  Auth is HTTP Basic
with an app-specific password loaded from
``~/.config/ts4k/caldav/<email>/credentials.json``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ts4k.adapters.base import BaseAdapter
from ts4k.auth.caldav import load_credentials
from ts4k.core.levels import AccessLevel, check_level, parse_level

logger = logging.getLogger(__name__)


@dataclass
class CaldavAdapterConfig:
    """Configuration for a CalDAV calendar source."""

    email: str            # account identity (Apple ID)
    server_url: str       # e.g. https://caldav.icloud.com
    calendar_id: str      # CalDAV calendar URL
    calendar_name: str = ""
    timezone: str = "UTC"
    config_dir: Path | None = None
    level: str = "readonly"


class CaldavAdapter(BaseAdapter):
    """Generic CalDAV calendar adapter (iCloud, Fastmail, Nextcloud, ...)."""

    def __init__(self, config: CaldavAdapterConfig, prefix: str = "cc") -> None:
        self._config = config
        self._prefix = prefix
        self._access_level = parse_level(config.level)
        self._client: Any = None
        self._principal: Any = None
        self._calendar: Any = None

    @property
    def source_prefix(self) -> str:
        return self._prefix

    # -- Connection ------------------------------------------------------------

    async def connect(self) -> None:
        import caldav
        from caldav.lib.error import AuthorizationError

        email = self._config.email
        creds = load_credentials(email, self._config.config_dir)
        if creds is None:
            raise RuntimeError(
                f"No CalDAV credentials for {email} — an app-specific password is "
                f"required (generate at https://account.apple.com, then run: "
                f"ts4k src add <prefix> apple email={email})"
            )

        def _connect() -> tuple[Any, Any]:
            client = caldav.DAVClient(
                url=creds.get("server_url") or self._config.server_url,
                username=creds["username"],
                password=creds["app_password"],
            )
            return client, client.principal()

        try:
            self._client, self._principal = await asyncio.to_thread(_connect)
        except AuthorizationError as e:
            raise RuntimeError(
                f"CalDAV auth failed for {email} — the app-specific password may be "
                f"revoked (they expire when the Apple ID password changes). Generate "
                f"a new one at https://account.apple.com, delete "
                f"~/.config/ts4k/caldav/{email}/credentials.json, and re-run "
                f"ts4k src add."
            ) from e

    async def disconnect(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
        self._client = None
        self._principal = None
        self._calendar = None

    async def __aenter__(self) -> CaldavAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()

    async def _get_calendar(self) -> Any:
        """Resolve and cache the configured calendar by URL."""
        if self._calendar is None:
            wanted = self._config.calendar_id.rstrip("/")

            def _find() -> Any:
                for c in self._principal.calendars():
                    if str(c.url).rstrip("/") == wanted:
                        return c
                raise RuntimeError(
                    f"Calendar {self._config.calendar_id!r} not found for "
                    f"{self._config.email}"
                )

            self._calendar = await asyncio.to_thread(_find)
        return self._calendar

    # -- Messaging stubs (calendar sources have no messages) -------------------

    async def whatsnew(self, since: str | None = None,
                       sender: str | None = None,
                       domain: str | None = None) -> list[dict]:
        return []

    async def list_messages(self, query: str | None = None,
                            count: int = 20,
                            page_token: str | None = None,
                            sender: str | None = None,
                            domain: str | None = None) -> list[dict]:
        return []

    async def read_message(self, msg_id: str) -> dict:
        raise NotImplementedError("CaldavAdapter does not support read_message")

    async def read_thread(self, thread_id: str) -> dict:
        raise NotImplementedError("CaldavAdapter does not support read_thread")

    # -- Level checks ----------------------------------------------------------

    def _check_modify(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.MODIFY, operation, provider="caldav")

    def _check_draft(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.DRAFT, operation, provider="caldav")

    def _check_send(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.SEND, operation, provider="caldav")

    # -- Helpers ---------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        if prefixed_id.startswith(f"{self._prefix}:"):
            return prefixed_id[len(self._prefix) + 1:]
        return prefixed_id

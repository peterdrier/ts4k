"""Google Calendar adapter — direct Google Calendar API v3."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ts4k.adapters.base import BaseAdapter
from ts4k.core.levels import AccessLevel, check_level, parse_level, scopes_for

logger = logging.getLogger(__name__)


@dataclass
class GcalAdapterConfig:
    """Configuration for a Google Calendar source."""

    email: str
    calendar_id: str
    calendar_name: str
    timezone: str = "UTC"
    config_dir: Path | None = None
    level: str = "readonly"


class GcalAdapter(BaseAdapter):
    """Google Calendar adapter using Calendar API v3."""

    def __init__(self, config: GcalAdapterConfig, prefix: str = "gc") -> None:
        self._config = config
        self._prefix = prefix
        self._access_level = parse_level(config.level)
        self._service: Any | None = None

    # -- BaseAdapter required properties ---------------------------------------

    @property
    def source_prefix(self) -> str:
        return self._prefix

    # access_level is inherited from BaseAdapter (reads self._access_level)

    # -- Lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        from ts4k.auth.google import build_calendar_service

        scopes = scopes_for("gcal", self._access_level)
        self._service = await asyncio.to_thread(
            build_calendar_service,
            email=self._config.email,
            config_dir=self._config.config_dir,
            scopes=scopes,
        )
        logger.info(
            "GcalAdapter connected for %s / %s (level=%s)",
            self._config.email,
            self._config.calendar_name,
            self._access_level.name.lower(),
        )

    async def disconnect(self) -> None:
        if self._service is not None:
            try:
                self._service.close()
            except Exception:
                pass
            self._service = None
            logger.info("GcalAdapter disconnected")

    async def __aenter__(self) -> GcalAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()

    # -- Messaging stubs (return empty for --source all safety) ----------------

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
        raise NotImplementedError("GcalAdapter does not support read_message")

    async def read_thread(self, thread_id: str) -> dict:
        raise NotImplementedError("GcalAdapter does not support read_thread")

    # -- Level checks ----------------------------------------------------------

    def _check_modify(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.MODIFY, operation, provider="gcal")

    def _check_draft(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.DRAFT, operation, provider="gcal")

    def _check_send(self, operation: str) -> None:
        check_level(self._access_level, AccessLevel.SEND, operation, provider="gcal")

    # -- Helpers ---------------------------------------------------------------

    def _strip_prefix(self, prefixed_id: str) -> str:
        """Remove the source prefix from an event ID."""
        if prefixed_id.startswith(f"{self._prefix}:"):
            return prefixed_id[len(self._prefix) + 1:]
        return prefixed_id

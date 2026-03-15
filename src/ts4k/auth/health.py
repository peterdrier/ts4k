"""Shared data types for token health validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TokenHealth:
    """Result of a lightweight token validation check."""
    status: str       # "ok", "auth", "error", "na"
    expiry: datetime | None
    scopes: list[str]
    detail: str       # human-readable status line

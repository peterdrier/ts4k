# src/ts4k/core/levels.py
"""Permission levels for ts4k source connections.

Each source has an access level that gates which operations are allowed
and determines which OAuth scopes are requested at auth time.

Levels are cumulative — each level includes all capabilities of lower levels:
  readonly (default) — read messages, list, search
  modify             — readonly + archive, label, mark read/unread, trash
  draft              — modify + create draft messages
  send               — draft + send messages (INTENTIONALLY NOT IMPLEMENTED)

The send level is defined to complete the permission model and reserve
the OAuth scope mapping, but ts4k does not implement any send capability.
This is by design — ts4k is the data layer, not the action layer.
"""

from __future__ import annotations

from enum import IntEnum


class AccessLevel(IntEnum):
    """Permission tier for a source connection."""
    READONLY = 0
    MODIFY = 1
    DRAFT = 2
    SEND = 3  # Defined but NOT IMPLEMENTED. ts4k never sends messages.


def parse_level(value: str | None) -> AccessLevel:
    """Parse a level string to an AccessLevel. None defaults to READONLY."""
    if value is None:
        return AccessLevel.READONLY
    return AccessLevel[value.upper()]


def check_level(current: AccessLevel, required: AccessLevel, operation: str,
                *, provider: str | None = None) -> None:
    """Raise PermissionError if current level is below required.

    SEND level is blocked for messaging providers (ts4k never sends messages)
    but permitted for calendar providers (invites are a normal workflow).
    """
    if required >= AccessLevel.SEND:
        if provider != "gcal":
            raise NotImplementedError(
                f"Operation '{operation}' requires level 'send', which is "
                "intentionally not implemented for messaging. "
                "ts4k never sends messages."
            )
    if current < required:
        raise PermissionError(
            f"Operation '{operation}' requires level='{required.name.lower()}', "
            f"but source is configured as level='{current.name.lower()}'. "
            f"Update with: ts4k src add <prefix> <provider> level={required.name.lower()}"
        )


# -- OAuth scope maps per provider per level --------------------------------

_GMAIL_SCOPES: dict[AccessLevel, list[str]] = {
    AccessLevel.READONLY: ["https://www.googleapis.com/auth/gmail.readonly"],
    AccessLevel.MODIFY: ["https://www.googleapis.com/auth/gmail.modify"],
    AccessLevel.DRAFT: ["https://www.googleapis.com/auth/gmail.modify"],
    AccessLevel.SEND: ["https://www.googleapis.com/auth/gmail.modify"],
}

_GCAL_SCOPES: dict[AccessLevel, list[str]] = {
    AccessLevel.READONLY: ["https://www.googleapis.com/auth/calendar.readonly"],
    AccessLevel.MODIFY: ["https://www.googleapis.com/auth/calendar"],
    AccessLevel.DRAFT: ["https://www.googleapis.com/auth/calendar"],
    AccessLevel.SEND: ["https://www.googleapis.com/auth/calendar"],
}

_O365_SCOPES: dict[AccessLevel, list[str]] = {
    AccessLevel.READONLY: [
        "https://graph.microsoft.com/Mail.Read",
        "https://graph.microsoft.com/Mail.Read.Shared",
        "https://graph.microsoft.com/User.Read",
    ],
    AccessLevel.MODIFY: [
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/Mail.Read.Shared",
        "https://graph.microsoft.com/User.Read",
    ],
    AccessLevel.DRAFT: [
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/Mail.Read.Shared",
        "https://graph.microsoft.com/User.Read",
    ],
    AccessLevel.SEND: [
        "https://graph.microsoft.com/Mail.ReadWrite",
        "https://graph.microsoft.com/Mail.Send",
        "https://graph.microsoft.com/Mail.Read.Shared",
        "https://graph.microsoft.com/User.Read",
    ],
}


def scopes_for(provider: str, level: AccessLevel) -> list[str]:
    """Return the OAuth scopes required for a provider at a given level."""
    provider = provider.lower()
    if provider == "gmail":
        return list(_GMAIL_SCOPES.get(level, []))
    if provider == "o365":
        return list(_O365_SCOPES.get(level, []))
    if provider == "gcal":
        return list(_GCAL_SCOPES.get(level, []))
    return []

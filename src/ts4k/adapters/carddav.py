"""CardDAV contacts adapter — read-only, RFC 6352, Apple/iCloud preset.

Fetches vCards over CardDAV and parses them into contact records that
seed the cross-platform identity map (``ts4k contacts sync``).  Auth is
HTTP Basic with the same app-specific password the CalDAV adapter uses
(``~/.config/ts4k/caldav/<email>/credentials.json``) — Apple issues one
password per Apple ID, not per service, so an already-configured
calendar account needs no new credential.

Deliberately **not** a ``BaseAdapter``: contacts are not messages, so
this adapter is unreachable from ``whatsnew``, ``list``, and the message
cache.  ``ts4k contacts sync`` is its only caller.

Read-only by construction — every request is a PROPFIND or a REPORT.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from ts4k.auth.caldav import ICLOUD_CARDDAV_URL, load_credentials

logger = logging.getLogger(__name__)

_DAV = "DAV:"
_CARD = "urn:ietf:params:xml:ns:carddav"

WHATSAPP_SUFFIX = "@s.whatsapp.net"

_PROPFIND_PRINCIPAL = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>"""

_PROPFIND_HOME_SET = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
<d:prop><c:addressbook-home-set/></d:prop></d:propfind>"""

_PROPFIND_COLLECTIONS = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>"""

# An empty filter matches every card in the collection (RFC 6352 §8.6).
_ADDRESSBOOK_QUERY = """<?xml version="1.0" encoding="utf-8"?>
<c:addressbook-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:carddav">
<d:prop><c:address-data/></d:prop><c:filter/></c:addressbook-query>"""

# iCloud redirects the well-known host to a per-account shard on a
# different origin; bounded to stay well clear of any redirect loop.
_MAX_REDIRECTS = 5
_REDIRECT_STATUS_CODES = (301, 302, 303, 307, 308)


# ---------------------------------------------------------------------------
# vCard parsing
# ---------------------------------------------------------------------------


_EXTENSION_RE = re.compile(r"\bext\.?\b|\bextension\b|x\d+\s*$", re.IGNORECASE)
_TEL_EXT_PARAM_RE = re.compile(r";\s*(?:ext|extension)\s*=", re.IGNORECASE)


def normalize_phone(raw: str) -> str | None:
    """Convert an address-book phone number to a WhatsApp JID.

    Returns ``<E164 digits>@s.whatsapp.net``, or ``None`` when the number
    carries no country code — a guessed country code would fabricate a
    JID pointing at a stranger — or when it doesn't cleanly resolve to a
    single E.164 number (an extension, or a digit count outside 7-15).
    """
    value = raw.strip()
    is_tel_uri = value.lower().startswith("tel:")
    if is_tel_uri:
        value = value[4:]
        if _TEL_EXT_PARAM_RE.search(value):
            # A tel: URI's ;ext= parameter (RFC 3966) is stripped below
            # along with the rest of the params — check for it first, or
            # a number with an extension would be linked as if it had none.
            return None
    value = value.split(";", 1)[0].strip()

    if _EXTENSION_RE.search(value):
        # Concatenating the extension's digits would fabricate a JID
        # pointing at an unrelated number — skip rather than guess.
        return None

    international = value.startswith("+")
    digits = "".join(c for c in value if c.isdigit())
    if not international and digits.startswith("00"):
        digits = digits[2:]
        international = True

    if not international or len(digits) < 7 or len(digits) > 15:
        return None
    return f"{digits}{WHATSAPP_SUFFIX}"


def _unfold(text: str) -> list[str]:
    """Join RFC 6350 folded continuation lines (leading space or tab)."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if lines and raw[:1] in (" ", "\t"):
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _split_unescaped(value: str, sep: str = ";") -> list[str]:
    """Split a vCard structured value on *sep*, skipping backslash-escaped ones.

    ``N:Doe\\;Smith;John;;;`` has an escaped literal ``;`` in the family
    name component — splitting on every ``;`` before unescaping would treat
    it as a component boundary instead.
    """
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in value:
        if escaped:
            current.append(ch)
            escaped = False
        elif ch == "\\":
            current.append(ch)
            escaped = True
        elif ch == sep:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _unescape(value: str) -> str:
    """Resolve vCard backslash escapes (``\\,`` ``\\;`` ``\\n``)."""
    out: list[str] = []
    chars = iter(value)
    for ch in chars:
        if ch == "\\":
            nxt = next(chars, "")
            out.append("\n" if nxt in ("n", "N") else nxt)
        else:
            out.append(ch)
    return "".join(out)


def _name_from_n(value: str) -> str:
    """Build a display name from a structured ``N`` value."""
    parts = [_unescape(p).strip() for p in _split_unescaped(value)]
    family = parts[0] if parts else ""
    given = parts[1] if len(parts) > 1 else ""
    return " ".join(p for p in (given, family) if p)


def parse_vcards(text: str) -> list[dict]:
    """Parse a vCard stream into ``{display_name, phones, emails}`` records.

    Phone values are returned as written in the card; ``normalize_phone``
    turns them into WhatsApp JIDs.  Records with no usable name come back
    with an empty ``display_name`` — the caller decides what to skip.
    """
    records: list[dict] = []
    fn = ""
    n_name = ""
    phones: list[str] = []
    emails: list[str] = []
    open_card = False

    for line in _unfold(text):
        stripped = line.strip()
        upper = stripped.upper()

        if upper == "BEGIN:VCARD":
            fn, n_name, phones, emails, open_card = "", "", [], [], True
            continue
        if not open_card:
            continue
        if upper == "END:VCARD":
            records.append({
                "display_name": fn or n_name,
                "phones": phones,
                "emails": emails,
            })
            open_card = False
            continue

        name, sep, value = stripped.partition(":")
        if not sep or not value.strip():
            continue
        # Strip the group prefix and parameters: "item1.TEL;type=CELL" -> "TEL"
        prop = name.split(";", 1)[0].split(".")[-1].upper()

        if prop == "FN":
            fn = _unescape(value).strip()
        elif prop == "N" and not n_name:
            n_name = _name_from_n(value)
        elif prop == "TEL":
            phone = value.strip()
            if phone not in phones:
                phones.append(phone)
        elif prop == "EMAIL":
            email = _unescape(value).strip().lower()
            if email and email not in emails:
                emails.append(email)

    return records


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass
class CarddavAdapterConfig:
    """Configuration for a CardDAV contacts source."""

    email: str                            # account identity (Apple ID)
    server_url: str = ICLOUD_CARDDAV_URL  # e.g. https://contacts.icloud.com
    config_dir: Path | None = None


class CarddavAdapter:
    """Read-only CardDAV client (iCloud, Fastmail, Nextcloud, ...)."""

    def __init__(self, config: CarddavAdapterConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._credential_key: str | None = None

    # -- Connection ------------------------------------------------------------

    async def connect(self) -> None:
        email = self._config.email
        # iCloud shares one app-specific password across CalDAV and CardDAV
        # (Apple issues it per Apple ID, not per service), so it reads the
        # plain-email credential file.  A generic CardDAV server has no such
        # guarantee — it may need different credentials than a CalDAV source
        # for the same email — so it gets its own service-scoped key.
        self._credential_key = (
            email if self._config.server_url == ICLOUD_CARDDAV_URL
            else f"{email}#carddav"
        )
        creds = load_credentials(self._credential_key, self._config.config_dir)
        if creds is None:
            raise RuntimeError(
                f"No CardDAV credentials for {email} — an app-specific password is "
                f"required (generate at https://account.apple.com, then run: "
                f"ts4k src add <prefix> apple-contacts email={email})"
            )
        # The stored server_url is the CalDAV endpoint; CardDAV lives on its
        # own host, so the adapter config wins.
        # follow_redirects=False: `_dav` handles redirects itself so the
        # per-account shard redirect stays authenticated (see its docstring).
        self._client = httpx.AsyncClient(
            auth=(creds["username"], creds["app_password"]),
            follow_redirects=False,
            timeout=30.0,
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> CarddavAdapter:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "CarddavAdapter is not connected. Call connect() or use "
                "'async with adapter:' first."
            )
        return self._client

    # -- DAV plumbing ----------------------------------------------------------

    async def _dav(
        self, method: str, url: str, body: str, depth: str
    ) -> tuple[ET.Element, str]:
        """Issue a PROPFIND/REPORT and return ``(xml root, final url)``.

        The final URL is the base for resolving relative hrefs — iCloud
        redirects the well-known host to a per-account shard.

        Redirects are followed by hand, re-issuing the same authenticated
        request against the new URL, rather than via httpx's
        ``follow_redirects`` — httpx strips the Authorization header on a
        cross-origin redirect, and the shard is a different origin.
        """
        client = self._require_client()
        content = body.encode("utf-8")
        headers = {
            "Content-Type": "application/xml; charset=utf-8",
            "Depth": depth,
        }
        for _ in range(_MAX_REDIRECTS):
            resp = await client.request(method, url, content=content, headers=headers)
            if resp.status_code in _REDIRECT_STATUS_CODES:
                location = resp.headers.get("Location")
                if not location:
                    break
                new_url = urljoin(str(resp.url), location)
                if resp.url.scheme == "https" and urlsplit(new_url).scheme != "https":
                    raise RuntimeError(
                        f"Refusing to follow a redirect from {resp.url} to {new_url} — "
                        f"downgrading from HTTPS to a non-HTTPS URL would send the "
                        f"CardDAV credentials in cleartext."
                    )
                url = new_url
                continue
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    f"CardDAV auth failed for {self._config.email} — the app-specific "
                    f"password may be revoked (they expire when the Apple ID password "
                    f"changes). Generate a new one at https://account.apple.com, delete "
                    f"~/.config/ts4k/caldav/{self._credential_key}/credentials.json, and "
                    f"re-run ts4k src add."
                )
            resp.raise_for_status()
            return ET.fromstring(resp.content), str(resp.url)
        raise RuntimeError(
            f"Too many redirects ({_MAX_REDIRECTS}) fetching {url} for "
            f"{self._config.email} — the CardDAV server may be misconfigured"
        )

    async def _principal_url(self) -> str:
        root, base = await self._dav(
            "PROPFIND", self._config.server_url, _PROPFIND_PRINCIPAL, "0"
        )
        href = root.find(f".//{{{_DAV}}}current-user-principal/{{{_DAV}}}href")
        if href is None or not href.text:
            raise RuntimeError(
                f"{self._config.server_url} returned no principal URL — "
                f"is it a CardDAV server?"
            )
        return urljoin(base, href.text)

    async def _home_set_url(self, principal_url: str) -> str:
        root, base = await self._dav(
            "PROPFIND", principal_url, _PROPFIND_HOME_SET, "0"
        )
        href = root.find(f".//{{{_CARD}}}addressbook-home-set/{{{_DAV}}}href")
        if href is None or not href.text:
            raise RuntimeError(
                f"No address book home set for {self._config.email} — the account "
                f"may not have contacts enabled."
            )
        return urljoin(base, href.text)

    async def _addressbook_urls(self, home_set_url: str) -> list[str]:
        root, base = await self._dav(
            "PROPFIND", home_set_url, _PROPFIND_COLLECTIONS, "1"
        )
        urls: list[str] = []
        for response in root.iter(f"{{{_DAV}}}response"):
            if response.find(f".//{{{_CARD}}}addressbook") is None:
                continue
            href = response.find(f"{{{_DAV}}}href")
            if href is not None and href.text:
                urls.append(urljoin(base, href.text))
        return urls

    async def _fetch_cards(self, addressbook_url: str) -> list[str]:
        root, _ = await self._dav(
            "REPORT", addressbook_url, _ADDRESSBOOK_QUERY, "1"
        )
        return [
            data.text
            for data in root.iter(f"{{{_CARD}}}address-data")
            if data.text
        ]

    # -- Contacts --------------------------------------------------------------

    async def list_contacts(self) -> list[dict]:
        """Fetch every vCard in the account as a parsed contact record."""
        principal = await self._principal_url()
        home_set = await self._home_set_url(principal)
        books = await self._addressbook_urls(home_set)
        if not books:
            logger.warning("No address books found for %s", self._config.email)

        records: list[dict] = []
        for url in books:
            for card in await self._fetch_cards(url):
                records.extend(parse_vcards(card))
        return records

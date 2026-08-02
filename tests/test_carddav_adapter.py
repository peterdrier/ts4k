"""Tests for the CardDAV contacts adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ts4k.adapters.carddav import (
    _MAX_REDIRECTS,
    CarddavAdapter,
    CarddavAdapterConfig,
    carddav_credential_key,
    normalize_phone,
    parse_vcards,
)
from ts4k.auth.caldav import (
    ICLOUD_CALDAV_URL,
    ICLOUD_CARDDAV_URL,
    credentials_path,
    save_credentials,
)


@pytest.fixture
def carddav_config(tmp_path: Path) -> CarddavAdapterConfig:
    save_credentials(
        "test@icloud.com",
        username="test@icloud.com",
        app_password="abcd-efgh",
        server_url=ICLOUD_CALDAV_URL,
        config_dir=tmp_path,
    )
    return CarddavAdapterConfig(
        email="test@icloud.com",
        server_url=ICLOUD_CARDDAV_URL,
        config_dir=tmp_path,
    )


class TestConnect:
    async def test_connect_without_credentials_raises_actionable(self, tmp_path: Path):
        config = CarddavAdapterConfig(email="nobody@icloud.com", config_dir=tmp_path)
        a = CarddavAdapter(config)
        with pytest.raises(RuntimeError, match="app-specific password"):
            await a.connect()

    async def test_reuses_the_caldav_credential_for_the_same_apple_id(
        self, carddav_config: CarddavAdapterConfig
    ):
        """The fixture stored a CalDAV credential — CardDAV needs no new one."""
        a = CarddavAdapter(carddav_config)
        await a.connect()
        try:
            assert a._client is not None
        finally:
            await a.disconnect()

    async def test_uses_the_carddav_host_not_the_stored_caldav_url(
        self, carddav_config: CarddavAdapterConfig
    ):
        """credentials.json holds the CalDAV endpoint; the config must win."""
        a = CarddavAdapter(carddav_config)
        a._client = _fake_client({})
        with pytest.raises(KeyError):
            await a._principal_url()
        method, url = a._client.request.call_args.args
        assert (method, url) == ("PROPFIND", ICLOUD_CARDDAV_URL)

    def test_requires_connect_before_use(self, carddav_config: CarddavAdapterConfig):
        a = CarddavAdapter(carddav_config)
        with pytest.raises(RuntimeError, match="not connected"):
            a._require_client()

    async def test_generic_server_does_not_reuse_the_caldav_credential(
        self, tmp_path: Path
    ):
        """A non-iCloud CardDAV server must not connect using a CalDAV
        credential stored for the same email — different service, possibly
        a different password."""
        save_credentials(
            "test@fastmail.com", username="test@fastmail.com",
            app_password="caldav-pw", server_url="https://caldav.fastmail.com/",
            config_dir=tmp_path,
        )
        config = CarddavAdapterConfig(
            email="test@fastmail.com",
            server_url="https://carddav.fastmail.com/",
            config_dir=tmp_path,
        )
        a = CarddavAdapter(config)
        with pytest.raises(RuntimeError, match="No CardDAV credentials"):
            await a.connect()

    async def test_generic_server_uses_its_own_scoped_credential(self, tmp_path: Path):
        save_credentials(
            "test@fastmail.com#carddav:carddav.fastmail.com", username="test@fastmail.com",
            app_password="carddav-pw", server_url="",
            config_dir=tmp_path,
        )
        config = CarddavAdapterConfig(
            email="test@fastmail.com",
            server_url="https://carddav.fastmail.com/",
            config_dir=tmp_path,
        )
        a = CarddavAdapter(config)
        await a.connect()
        try:
            assert a._client is not None
        finally:
            await a.disconnect()

    async def test_401_message_names_the_config_dir_scoped_credential_path(
        self, carddav_config: CarddavAdapterConfig
    ):
        """The auth-failure message must point at the credential file under
        this source's config_dir, not the global default — a source with a
        custom config_dir stores its credential there, not at ~/.config/ts4k."""
        a = CarddavAdapter(carddav_config)
        a._client = _fake_client(_ROUTES, status_code=401)
        with pytest.raises(RuntimeError) as excinfo:
            await a.list_contacts()
        expected_path = str(credentials_path("test@icloud.com", carddav_config.config_dir))
        assert expected_path in str(excinfo.value)


class TestCredentialKey:
    def test_icloud_uses_the_plain_email(self):
        assert carddav_credential_key("a@icloud.com", ICLOUD_CARDDAV_URL) == "a@icloud.com"

    def test_generic_server_key_is_scoped_by_email_and_host(self):
        key = carddav_credential_key("me@fastmail.com", "https://carddav.fastmail.com/")
        assert key == "me@fastmail.com#carddav:carddav.fastmail.com"

    def test_same_email_different_hosts_get_distinct_keys(self):
        """Two generic CardDAV sources sharing an email but pointed at
        different servers must not collide on the same credential file —
        each needs its own password prompt and its own stored credential."""
        key_a = carddav_credential_key("me@example.com", "https://carddav.example-a.com/")
        key_b = carddav_credential_key("me@example.com", "https://carddav.example-b.com/")
        assert key_a != key_b


class TestRedirectHandling:
    """iCloud redirects the well-known host to a per-account shard on a
    different origin.  httpx's automatic follow_redirects strips the
    Authorization header on a cross-origin hop, so `_dav` follows
    redirects itself, re-issuing a fresh (authenticated) top-level
    request for each hop — verified here against a real httpx.AsyncClient
    over a mock transport, since the auth-stripping behavior lives inside
    httpx itself and a hand-rolled fake client wouldn't exercise it."""

    def _adapter(self, tmp_path: Path, handler) -> CarddavAdapter:
        config = CarddavAdapterConfig(email="test@icloud.com", config_dir=tmp_path)
        a = CarddavAdapter(config)
        a._client = httpx.AsyncClient(
            auth=("test@icloud.com", "abcd-efgh"),
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )
        return a

    async def test_redirect_to_different_origin_preserves_auth(self, tmp_path: Path):
        shard_url = "https://p01-contacts.icloud.com/1234/principal/"
        seen_auth: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_auth.append(request.headers.get("authorization"))
            if str(request.url) == ICLOUD_CARDDAV_URL:
                return httpx.Response(301, headers={"Location": shard_url})
            assert str(request.url) == shard_url
            return httpx.Response(207, content=PRINCIPAL_XML.encode())

        a = self._adapter(tmp_path, handler)
        root, base = await a._dav("PROPFIND", ICLOUD_CARDDAV_URL, "<propfind/>", "0")

        assert base == shard_url
        assert len(seen_auth) == 2
        # Both hops are authenticated, and with the SAME credentials —
        # follow_redirects=True would leave seen_auth[1] as None because
        # the shard is a different origin.
        assert seen_auth[0] is not None
        assert seen_auth[1] == seen_auth[0]

    async def test_redirect_loop_errors_after_hop_limit(self, tmp_path: Path):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                302, headers={"Location": "https://p01-contacts.icloud.com/loop"}
            )

        a = self._adapter(tmp_path, handler)
        with pytest.raises(RuntimeError, match="Too many redirects"):
            await a._dav("PROPFIND", ICLOUD_CARDDAV_URL, "<propfind/>", "0")
        assert call_count == _MAX_REDIRECTS

    async def test_https_to_http_redirect_is_rejected(self, tmp_path: Path):
        """A server redirecting an authenticated https request to a plain
        http URL must not have the request re-issued against it — that
        would send the CardDAV credentials in cleartext."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                302, headers={"Location": "http://evil.example/shard/"}
            )

        a = self._adapter(tmp_path, handler)
        with pytest.raises(RuntimeError, match="HTTPS"):
            await a._dav("PROPFIND", ICLOUD_CARDDAV_URL, "<propfind/>", "0")
        # Only the original https request went out — the downgraded http
        # URL never received a second (authenticated) request.
        assert call_count == 1

    async def test_same_origin_https_redirect_is_followed(self, tmp_path: Path):
        """A generic (non-iCloud) CardDAV server redirecting within its own
        host — e.g. adding a trailing slash or moving to a sibling path —
        is a routine same-origin hop and must still be followed."""
        config = CarddavAdapterConfig(
            email="test@fastmail.com", server_url="https://carddav.fastmail.com/",
            config_dir=tmp_path,
        )
        a = CarddavAdapter(config)
        moved_url = "https://carddav.fastmail.com/dav/principal/"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == "https://carddav.fastmail.com/":
                return httpx.Response(301, headers={"Location": moved_url})
            assert str(request.url) == moved_url
            return httpx.Response(207, content=PRINCIPAL_XML.encode())

        a._client = httpx.AsyncClient(
            auth=("test@fastmail.com", "carddav-pw"),
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )
        root, base = await a._dav(
            "PROPFIND", "https://carddav.fastmail.com/", "<propfind/>", "0"
        )
        assert base == moved_url

    async def test_redirect_to_an_unrelated_https_host_is_rejected(self, tmp_path: Path):
        """A non-iCloud server (or a compromised/misconfigured one) redirecting
        to a completely different host must not have credentials re-sent to
        it — only same-host hops, or an iCloud account's own shard hosts, are
        trusted."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                302, headers={"Location": "https://evil.example/steal-creds/"}
            )

        a = self._adapter(tmp_path, handler)
        with pytest.raises(RuntimeError, match="not trusted"):
            await a._dav("PROPFIND", ICLOUD_CARDDAV_URL, "<propfind/>", "0")
        # Only the original request went out — the untrusted host never
        # received a second (authenticated) request.
        assert call_count == 1


class TestNormalizePhone:
    def test_international_with_punctuation(self):
        assert normalize_phone("+1 (555) 123-4567") == "15551234567@s.whatsapp.net"

    def test_double_zero_prefix(self):
        assert normalize_phone("0031 6 12345678") == "31612345678@s.whatsapp.net"

    def test_tel_uri_with_ext_param_is_rejected(self):
        # ext= is a real RFC 3966 extension — concatenating the base number
        # would link a JID for a different, unrelated number.
        assert normalize_phone("tel:+15551234567;ext=99") is None

    def test_tel_uri_with_non_ext_param_is_accepted(self):
        assert normalize_phone("tel:+15551234567;type=cell") == "15551234567@s.whatsapp.net"

    def test_already_bare_e164(self):
        assert normalize_phone("+15551234567") == "15551234567@s.whatsapp.net"

    def test_local_number_without_country_code_is_rejected(self):
        # Guessing a country code would fabricate a JID pointing at a stranger
        assert normalize_phone("555-123-4567") is None

    def test_digits_without_plus_are_rejected(self):
        assert normalize_phone("15551234567") is None

    def test_too_short_is_rejected(self):
        assert normalize_phone("+123") is None

    def test_empty_is_rejected(self):
        assert normalize_phone("   ") is None

    def test_too_long_is_rejected(self):
        # Beyond E.164's 15-digit max
        assert normalize_phone("+1234567890123456") is None

    def test_max_length_e164_is_accepted(self):
        assert normalize_phone("+123456789012345") == "123456789012345@s.whatsapp.net"

    def test_extension_word_is_rejected(self):
        # Concatenating the extension would fabricate a JID for a
        # different number entirely — skip rather than guess.
        assert normalize_phone("+1 555 123 4567 ext 89") is None

    def test_extension_abbreviation_with_dot_is_rejected(self):
        assert normalize_phone("+1 555 123 4567 ext. 89") is None

    def test_trailing_x_extension_is_rejected(self):
        assert normalize_phone("+1 555 123 4567 x89") is None

    def test_extension_word_full_is_rejected(self):
        assert normalize_phone("+1 555 123 4567 extension 89") is None


SIMPLE_VCARD = """BEGIN:VCARD
VERSION:3.0
N:Connor;Sarah;;;
FN:Sarah Connor
item1.TEL;type=CELL;type=pref:+1 (555) 123-4567
TEL;type=HOME:+1 555 987 6543
item2.EMAIL;type=INTERNET;type=HOME:Sarah@Example.com
EMAIL;type=INTERNET;type=WORK:sarah@work.example
END:VCARD
"""

FOLDED_VCARD = """BEGIN:VCARD
VERSION:3.0
FN:Alexander Von Der Berg
PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJ
 CQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/
EMAIL:alex@example.com
END:VCARD
"""

NO_FN_VCARD = """BEGIN:VCARD
VERSION:3.0
N:Reyes;Maria;J;;
TEL:+34 600 123 456
END:VCARD
"""

NO_FN_ESCAPED_SEMICOLON_VCARD = """BEGIN:VCARD
VERSION:3.0
N:Doe\\;Smith;John;;;
END:VCARD
"""

ESCAPED_VCARD = """BEGIN:VCARD
VERSION:3.0
FN:Smith\\, Jr.\\; John
EMAIL:john@example.com
END:VCARD
"""

EMPTY_VCARD = """BEGIN:VCARD
VERSION:3.0
FN:Acme Storage Unit
END:VCARD
"""

NAMELESS_VCARD = """BEGIN:VCARD
VERSION:3.0
EMAIL:noname@example.com
END:VCARD
"""


class TestParseVcards:
    def test_display_name_phones_and_emails(self):
        (record,) = parse_vcards(SIMPLE_VCARD)
        assert record["display_name"] == "Sarah Connor"
        assert record["phones"] == ["+1 (555) 123-4567", "+1 555 987 6543"]
        assert record["emails"] == ["sarah@example.com", "sarah@work.example"]

    def test_group_prefixes_and_parameters_are_stripped(self):
        (record,) = parse_vcards(SIMPLE_VCARD)
        # item1.TEL and item2.EMAIL still land in the right buckets
        assert len(record["phones"]) == 2
        assert len(record["emails"]) == 2

    def test_folded_lines_are_unfolded(self):
        """A folded base64 PHOTO must not swallow the properties after it."""
        (record,) = parse_vcards(FOLDED_VCARD)
        assert record["display_name"] == "Alexander Von Der Berg"
        assert record["emails"] == ["alex@example.com"]

    def test_falls_back_to_structured_name(self):
        (record,) = parse_vcards(NO_FN_VCARD)
        assert record["display_name"] == "Maria Reyes"

    def test_structured_name_with_escaped_semicolon_is_not_split(self):
        """An escaped ';' inside a component is part of the value, not a
        component boundary — splitting on every ';' before unescaping
        would drop everything after it (see _split_unescaped)."""
        (record,) = parse_vcards(NO_FN_ESCAPED_SEMICOLON_VCARD)
        assert record["display_name"] == "John Doe;Smith"

    def test_escapes_are_resolved(self):
        (record,) = parse_vcards(ESCAPED_VCARD)
        assert record["display_name"] == "Smith, Jr.; John"

    def test_record_without_phone_or_email(self):
        (record,) = parse_vcards(EMPTY_VCARD)
        assert record == {
            "display_name": "Acme Storage Unit", "phones": [], "emails": []
        }

    def test_record_without_a_name(self):
        (record,) = parse_vcards(NAMELESS_VCARD)
        assert record["display_name"] == ""

    def test_multiple_cards_in_one_stream(self):
        records = parse_vcards(SIMPLE_VCARD + NO_FN_VCARD)
        assert [r["display_name"] for r in records] == ["Sarah Connor", "Maria Reyes"]

    def test_duplicate_values_are_deduplicated(self):
        card = (
            "BEGIN:VCARD\nFN:Dup\nTEL:+15551234567\nTEL:+15551234567\n"
            "EMAIL:d@x.com\nEMAIL:D@X.com\nEND:VCARD\n"
        )
        (record,) = parse_vcards(card)
        assert record["phones"] == ["+15551234567"]
        assert record["emails"] == ["d@x.com"]

    def test_crlf_line_endings(self):
        records = parse_vcards(SIMPLE_VCARD.replace("\n", "\r\n"))
        assert records[0]["display_name"] == "Sarah Connor"

    def test_empty_stream(self):
        assert parse_vcards("") == []


# ---------------------------------------------------------------------------
# Fetching — the full PROPFIND/REPORT walk, no network
# ---------------------------------------------------------------------------

PRINCIPAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<multistatus xmlns="DAV:"><response>
  <href>/</href>
  <propstat><prop><current-user-principal><href>/1234/principal/</href>
  </current-user-principal></prop><status>HTTP/1.1 200 OK</status></propstat>
</response></multistatus>"""

HOME_SET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<multistatus xmlns="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"><response>
  <href>/1234/principal/</href>
  <propstat><prop><card:addressbook-home-set>
  <href>https://p01-contacts.icloud.com/1234/carddavhome/</href>
  </card:addressbook-home-set></prop><status>HTTP/1.1 200 OK</status></propstat>
</response></multistatus>"""

COLLECTIONS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<multistatus xmlns="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
<response>
  <href>/1234/carddavhome/</href>
  <propstat><prop><resourcetype><collection/></resourcetype></prop>
  <status>HTTP/1.1 200 OK</status></propstat>
</response>
<response>
  <href>/1234/carddavhome/card/</href>
  <propstat><prop><resourcetype><collection/><card:addressbook/></resourcetype>
  </prop><status>HTTP/1.1 200 OK</status></propstat>
</response>
</multistatus>"""

CARDS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<multistatus xmlns="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
<response><href>/1234/carddavhome/card/a.vcf</href>
  <propstat><prop><card:address-data>{first}</card:address-data></prop>
  <status>HTTP/1.1 200 OK</status></propstat></response>
<response><href>/1234/carddavhome/card/b.vcf</href>
  <propstat><prop><card:address-data>{second}</card:address-data></prop>
  <status>HTTP/1.1 200 OK</status></propstat></response>
</multistatus>""".format(first=SIMPLE_VCARD, second=NO_FN_VCARD)

_HOME = "https://p01-contacts.icloud.com/1234/carddavhome/"
_BOOK = "https://p01-contacts.icloud.com/1234/carddavhome/card/"

_ROUTES = {
    ("PROPFIND", ICLOUD_CARDDAV_URL): (PRINCIPAL_XML, ICLOUD_CARDDAV_URL + "/"),
    ("PROPFIND", "https://contacts.icloud.com/1234/principal/"): (
        HOME_SET_XML, "https://contacts.icloud.com/1234/principal/"
    ),
    ("PROPFIND", _HOME): (COLLECTIONS_XML, _HOME),
    ("REPORT", _BOOK): (CARDS_XML, _BOOK),
}


class _FakeResponse:
    def __init__(self, body: str, url: str, status_code: int = 207) -> None:
        self.content = body.encode("utf-8")
        self.url = url
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_client(routes: dict, status_code: int = 207) -> MagicMock:
    """An httpx-shaped client that answers from *routes* and records calls."""

    async def _request(method, url, **kwargs):
        body, final_url = routes[(method, url)]
        return _FakeResponse(body, final_url, status_code)

    client = MagicMock()
    client.request = AsyncMock(side_effect=_request)
    return client


class TestListContacts:
    @pytest.fixture
    def adapter(self, carddav_config: CarddavAdapterConfig) -> CarddavAdapter:
        a = CarddavAdapter(carddav_config)
        # Bypass network: connect() would put a real httpx client here
        a._client = _fake_client(_ROUTES)
        return a

    async def test_walks_principal_home_and_addressbook(self, adapter: CarddavAdapter):
        records = await adapter.list_contacts()
        assert [r["display_name"] for r in records] == ["Sarah Connor", "Maria Reyes"]
        assert records[0]["emails"] == ["sarah@example.com", "sarah@work.example"]

    async def test_only_issues_read_only_methods(self, adapter: CarddavAdapter):
        """Read-only posture: nothing but PROPFIND and REPORT ever goes out."""
        await adapter.list_contacts()
        methods = {call.args[0] for call in adapter._client.request.call_args_list}
        assert methods == {"PROPFIND", "REPORT"}

    async def test_non_addressbook_collections_are_skipped(
        self, adapter: CarddavAdapter
    ):
        await adapter.list_contacts()
        urls = [call.args[1] for call in adapter._client.request.call_args_list]
        assert _BOOK in urls
        assert urls.count(_HOME) == 1  # the home collection is never REPORTed

    async def test_auth_failure_is_actionable(self, carddav_config):
        a = CarddavAdapter(carddav_config)
        a._client = _fake_client(_ROUTES, status_code=401)
        with pytest.raises(RuntimeError, match="app-specific password may be revoked"):
            await a.list_contacts()

    async def test_missing_principal_is_reported(self, carddav_config):
        empty = """<?xml version="1.0"?><multistatus xmlns="DAV:"/>"""
        a = CarddavAdapter(carddav_config)
        a._client = _fake_client(
            {("PROPFIND", ICLOUD_CARDDAV_URL): (empty, ICLOUD_CARDDAV_URL)}
        )
        with pytest.raises(RuntimeError, match="no principal URL"):
            await a.list_contacts()

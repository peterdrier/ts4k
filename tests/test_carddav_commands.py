"""Tests for CardDAV contact import in commands.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ts4k import commands
from ts4k.state import contacts, sources

CARDDAV_CFG = {
    "provider": "carddav",
    "email": "a@icloud.com",
    "server_url": "https://contacts.icloud.com",
}


def _record(name: str, phones: list[str] | None = None,
            emails: list[str] | None = None) -> dict:
    return {"display_name": name, "phones": phones or [], "emails": emails or []}


class TestMakeAdapter:
    def test_carddav_is_not_a_message_source(self):
        """Contacts must never reach whatsnew / list / the message cache."""
        assert commands._make_adapter("ic", dict(CARDDAV_CFG)) is None

    def test_no_unknown_provider_warning(self, caplog):
        commands._make_adapter("ic", dict(CARDDAV_CFG))
        assert "Unknown provider" not in caplog.text


class TestTokenHealth:
    def test_missing_credentials_point_at_src_add(self, ts4k_config):
        health = commands.check_token_health("ic", dict(CARDDAV_CFG))
        assert health.status == "na"
        assert "apple-contacts" in health.detail

    def test_shares_the_caldav_credential(self, ts4k_config):
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL, save_credentials

        save_credentials("a@icloud.com", username="a@icloud.com",
                         app_password="abcd-efgh-ijkl-mnop",
                         server_url=ICLOUD_CALDAV_URL)
        health = commands.check_token_health("ic", dict(CARDDAV_CFG))
        assert health.status == "ok"

    def test_generic_carddav_source_reports_healthy_under_its_scoped_key(self, ts4k_config):
        """A generic (non-iCloud) CardDAV source stores its credential
        under a service-scoped key, not the plain email — the health
        check must look there instead of reporting a false [na]."""
        from ts4k.auth.caldav import save_credentials

        cfg = {
            "provider": "carddav", "email": "me@fastmail.com",
            "server_url": "https://carddav.fastmail.com/",
        }
        save_credentials("me@fastmail.com#carddav#carddav.fastmail.com", username="me@fastmail.com",
                         app_password="carddav-pw", server_url="")

        health = commands.check_token_health("fm", cfg)
        assert health.status == "ok"

    def test_generic_carddav_source_ignores_a_same_email_caldav_credential(self, ts4k_config):
        """The plain-email credential belongs to CalDAV — a generic CardDAV
        source must not be reported healthy just because that exists."""
        from ts4k.auth.caldav import save_credentials

        cfg = {
            "provider": "carddav", "email": "me@fastmail.com",
            "server_url": "https://carddav.fastmail.com/",
        }
        save_credentials("me@fastmail.com", username="me@fastmail.com",
                         app_password="caldav-pw", server_url="https://caldav.fastmail.com/")

        health = commands.check_token_health("fm", cfg)
        assert health.status == "na"

    def test_icloud_carddav_health_check_is_unchanged(self, ts4k_config):
        health = commands.check_token_health("ic", dict(CARDDAV_CFG))
        assert health.status == "na"
        assert "apple-contacts" in health.detail


class TestPlanContactImport:
    def test_emails_and_phones_become_identifiers(self, ts4k_config):
        plan = commands._plan_contact_import(
            [_record("Sarah Connor", ["+1 (555) 123-4567"], ["sarah@example.com"])],
            {},
        )
        assert plan["links"] == [
            ("sarah connor",
             ["g:sarah@example.com", "w:15551234567@s.whatsapp.net"]),
        ]
        assert plan["conflicts"] == []

    def test_alias_is_lowercased_and_whitespace_collapsed(self, ts4k_config):
        plan = commands._plan_contact_import(
            [_record("  Sarah   CONNOR ", emails=["s@x.com"])], {}
        )
        assert plan["links"][0][0] == "sarah connor"

    def test_record_without_a_name_is_skipped(self, ts4k_config):
        plan = commands._plan_contact_import([_record("", emails=["x@y.com"])], {})
        assert plan["links"] == []
        assert plan["skipped"]["no name"] == 1

    def test_record_without_identifiers_is_skipped(self, ts4k_config):
        plan = commands._plan_contact_import([_record("Acme Storage")], {})
        assert plan["links"] == []
        assert plan["skipped"]["no phone or email"] == 1

    def test_local_phone_number_is_counted_as_skipped(self, ts4k_config):
        plan = commands._plan_contact_import(
            [_record("Bob", ["555-1234567"], ["bob@x.com"])], {}
        )
        assert plan["links"] == [("bob", ["g:bob@x.com"])]
        assert plan["skipped"]["phone number without a country code (+ or 00)"] == 1

    def test_existing_alias_is_a_conflict_not_an_overwrite(self, ts4k_config):
        existing = {"sarah": ["g:sarah@gmail.com"]}
        plan = commands._plan_contact_import(
            [_record("Sarah", emails=["sarah@work.example"])], existing
        )
        assert plan["links"] == []
        assert plan["conflicts"] == ["sarah|alias already exists"]

    def test_identifier_owned_by_another_alias_is_a_conflict(self, ts4k_config):
        existing = {"sarah": ["g:sarah@gmail.com"]}
        plan = commands._plan_contact_import(
            [_record("Sarah Connor", emails=["sarah@gmail.com"])], existing
        )
        assert plan["links"] == []
        assert plan["conflicts"] == [
            "sarah connor|g:sarah@gmail.com already linked to 'sarah'"
        ]

    def test_already_linked_record_is_skipped_quietly(self, ts4k_config):
        existing = {"sarah": ["g:sarah@gmail.com", "w:15551234567@s.whatsapp.net"]}
        plan = commands._plan_contact_import(
            [_record("Sarah", ["+15551234567"], ["sarah@gmail.com"])], existing
        )
        assert plan["links"] == []
        assert plan["conflicts"] == []
        assert plan["skipped"]["already up to date"] == 1

    def test_two_records_sharing_an_identifier_conflict(self, ts4k_config):
        plan = commands._plan_contact_import(
            [
                _record("Ann Smith", emails=["family@example.com"]),
                _record("Bob Smith", emails=["family@example.com"]),
            ],
            {},
        )
        assert plan["links"] == [("ann smith", ["g:family@example.com"])]
        assert plan["conflicts"] == [
            "bob smith|g:family@example.com already linked to 'ann smith'"
        ]

    def test_two_records_with_the_same_alias_do_not_silently_merge(self, ts4k_config):
        """Two distinct vCards that normalize to the same display name must
        not both become links for that alias — the second would silently
        link a stranger's identifiers into the first person's alias."""
        plan = commands._plan_contact_import(
            [
                _record("John Smith", emails=["john.a@example.com"]),
                _record("John Smith", emails=["john.b@example.com"]),
            ],
            {},
        )
        assert plan["links"] == [("john smith", ["g:john.a@example.com"])]
        assert plan["conflicts"] == ["john smith|duplicate in import"]

    def test_configured_prefixes_are_used_for_identifiers(self, ts4k_config):
        """Identifiers must land under the user's configured source
        prefixes, not the hardcoded canonical letters, or downstream
        filtering by source prefix finds nothing."""
        sources.add("gw", provider="gmail", email="a@gmail.com")
        sources.add("oy", provider="o365", email="a@work.example")
        sources.add("wa", provider="whatsapp", mcp_cwd="/tmp")
        plan = commands._plan_contact_import(
            [_record("Sarah Connor", ["+15551234567"], ["sarah@example.com"])], {}
        )
        assert plan["links"] == [
            ("sarah connor", [
                "gw:sarah@example.com",
                "oy:sarah@example.com",
                "wa:15551234567@s.whatsapp.net",
            ]),
        ]

    def test_identifier_differing_only_by_local_part_case_is_a_conflict_not_a_silent_merge(
        self, ts4k_config
    ):
        """core.normalize lowercases only the email domain, so a stored
        identifier and a freshly-imported one that differ solely in
        local-part case are not treated as equal. That must surface as a
        conflict — the existing link left untouched — rather than being
        silently accepted as if the import were already up to date."""
        existing = {"sarah": ["g:sarah@example.com"]}
        plan = commands._plan_contact_import(
            [_record("Sarah", emails=["Sarah@example.com"])], existing
        )
        assert plan["links"] == []
        assert plan["conflicts"] == ["sarah|alias already exists"]

    def test_no_matching_source_falls_back_to_canonical_letter(self, ts4k_config):
        """Without a configured gmail/whatsapp source, import must still
        work standalone using the canonical g:/w: letters."""
        plan = commands._plan_contact_import(
            [_record("Sarah Connor", ["+15551234567"], ["sarah@example.com"])], {}
        )
        assert plan["links"] == [
            ("sarah connor",
             ["g:sarah@example.com", "w:15551234567@s.whatsapp.net"]),
        ]


def _adapter_returning(records: list[dict]) -> MagicMock:
    adapter = MagicMock()
    adapter.__aenter__ = AsyncMock(return_value=adapter)
    adapter.__aexit__ = AsyncMock(return_value=None)
    adapter.list_contacts = AsyncMock(return_value=records)
    return adapter


@pytest.fixture
def carddav_source(ts4k_config, monkeypatch):
    """A configured CardDAV source whose adapter returns two records."""
    sources.add("ic", provider="carddav", **{
        k: v for k, v in CARDDAV_CFG.items() if k != "provider"
    })
    adapter = _adapter_returning([
        _record("Sarah Connor", ["+1 (555) 123-4567"], ["sarah@example.com"]),
        _record("Maria Reyes", ["+34 600 123 456"]),
    ])
    monkeypatch.setattr(
        "ts4k.adapters.carddav.CarddavAdapter", lambda config: adapter
    )
    return adapter


class TestSyncContacts:
    async def test_preview_writes_nothing(self, carddav_source):
        out = await commands.sync_contacts()
        assert "sarah connor|g:sarah@example.com|w:15551234567@s.whatsapp.net" in out
        assert "Re-run with --apply" in out
        assert contacts.list_all() == {}

    async def test_apply_commits_the_links(self, carddav_source):
        await commands.sync_contacts(apply=True)
        assert contacts.query("sarah connor") == [
            "g:sarah@example.com", "w:15551234567@s.whatsapp.net"
        ]
        assert contacts.query("maria reyes") == ["w:34600123456@s.whatsapp.net"]

    async def test_apply_reports_what_it_wrote(self, carddav_source):
        out = await commands.sync_contacts(apply=True)
        assert "Applied 2 links" in out
        assert "Re-run with --apply" not in out

    async def test_apply_leaves_a_conflicting_hand_made_link_intact(
        self, carddav_source
    ):
        contacts.link("sarah connor", "g:hand-made@example.com")
        await commands.sync_contacts(apply=True)
        assert contacts.query("sarah connor") == ["g:hand-made@example.com"]

    async def test_apply_is_idempotent(self, carddav_source):
        await commands.sync_contacts(apply=True)
        before = contacts.list_all()
        out = await commands.sync_contacts(apply=True)
        assert contacts.list_all() == before
        assert "already up to date" in out

    async def test_status_reflects_the_imported_contacts(self, carddav_source):
        assert "Contacts: 0 aliases, 0 identifiers" in commands.get_status()
        await commands.sync_contacts(apply=True)
        assert "Contacts: 2 aliases, 3 identifiers" in commands.get_status()

    async def test_import_does_not_touch_watermarks_or_cache(self, carddav_source):
        from ts4k.state import cache, keyed_watermarks

        await commands.sync_contacts(apply=True)
        assert cache.stats()["total"] == 0
        assert keyed_watermarks.get_all("life") == {}

    async def test_no_source_configured(self, ts4k_config):
        out = await commands.sync_contacts()
        assert "no CardDAV source configured" in out
        assert "apple-contacts" in out

    async def test_unknown_source_prefix(self, carddav_source):
        out = await commands.sync_contacts(source="nope")
        assert "not a CardDAV source" in out

    async def test_ambiguous_source_requires_a_choice(self, carddav_source):
        sources.add("ic2", provider="carddav", email="b@icloud.com")
        out = await commands.sync_contacts()
        assert "multiple CardDAV sources" in out
        assert contacts.list_all() == {}

    async def test_explicit_source_prefix(self, carddav_source):
        sources.add("ic2", provider="carddav", email="b@icloud.com")
        out = await commands.sync_contacts(source="ic")
        assert out.startswith("CardDAV ic (a@icloud.com): 2 records")

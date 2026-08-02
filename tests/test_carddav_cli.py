"""Tests for the CardDAV source-add and `contacts sync` CLI wiring."""

from __future__ import annotations

import argparse

from ts4k import cli
from ts4k.auth.caldav import (
    ICLOUD_CALDAV_URL,
    ICLOUD_CARDDAV_URL,
    credentials_path,
    load_credentials,
)
from ts4k.state import sources

PASSWORD = "abcd-efgh-ijkl-mnop"


def _args(provider: str, params: list[str]) -> argparse.Namespace:
    return argparse.Namespace(
        action="add", prefix="ic", provider=provider, params=params,
    )


def _refuse_prompt(prompt: str = "") -> str:
    raise AssertionError("should not prompt for a password")


class TestSrcAddAppleContacts:
    def test_prompts_saves_and_registers_the_source(self, ts4k_config, monkeypatch):
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": PASSWORD)
        cli._cmd_sources(_args("apple-contacts", ["email=a@icloud.com"]))

        cfg = sources.list_all()["ic"]
        assert cfg["provider"] == "carddav"
        assert cfg["email"] == "a@icloud.com"
        assert cfg["server_url"] == ICLOUD_CARDDAV_URL

        creds = load_credentials("a@icloud.com")
        assert creds is not None and creds["app_password"] == PASSWORD

    def test_fresh_credential_keeps_the_caldav_endpoint(self, ts4k_config, monkeypatch):
        """The shared credential file's server_url is read back by CalDAV."""
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": PASSWORD)
        cli._cmd_sources(_args("apple-contacts", ["email=a@icloud.com"]))
        assert load_credentials("a@icloud.com")["server_url"] == ICLOUD_CALDAV_URL

    def test_reuses_an_existing_caldav_credential(self, ts4k_config, monkeypatch, capsys):
        from ts4k.auth.caldav import save_credentials

        save_credentials("a@icloud.com", username="a@icloud.com",
                         app_password=PASSWORD, server_url=ICLOUD_CALDAV_URL)
        monkeypatch.setattr(cli, "_prompt_password", _refuse_prompt)

        cli._cmd_sources(_args("apple-contacts", ["email=a@icloud.com"]))

        assert "Reusing" in capsys.readouterr().out
        assert sources.list_all()["ic"]["provider"] == "carddav"

    def test_missing_email_prints_usage_and_adds_nothing(self, ts4k_config, capsys):
        cli._cmd_sources(_args("apple-contacts", []))
        assert "email" in capsys.readouterr().out
        assert "ic" not in sources.list_all()

    def test_aborted_prompt_adds_nothing(self, ts4k_config, monkeypatch):
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "")
        cli._cmd_sources(_args("apple-contacts", ["email=a@icloud.com"]))
        assert "ic" not in sources.list_all()
        assert load_credentials("a@icloud.com") is None

    def test_generic_carddav_server_url_is_kept(self, ts4k_config, monkeypatch):
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "any-password")
        cli._cmd_sources(_args("carddav", [
            "email=me@fastmail.com", "server_url=https://carddav.fastmail.com/",
        ]))
        cfg = sources.list_all()["ic"]
        assert cfg["server_url"] == "https://carddav.fastmail.com/"
        # Non-Apple servers skip the xxxx-xxxx-xxxx-xxxx format check.
        # Credentials land under a service-scoped key, not the plain email.
        assert load_credentials("me@fastmail.com#carddav")["app_password"] == "any-password"

    def test_generic_carddav_setup_does_not_poison_the_shared_credential(
        self, ts4k_config, monkeypatch
    ):
        """A non-iCloud CardDAV setup must never write to the shared
        per-email credential file — otherwise a later CalDAV source for the
        same email would connect to the CardDAV endpoint (CaldavAdapter.connect
        prefers a stored server_url over the source config)."""
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "any-password")
        cli._cmd_sources(_args("carddav", [
            "email=me@fastmail.com", "server_url=https://carddav.fastmail.com/",
        ]))
        assert load_credentials("me@fastmail.com") is None

    def test_config_dir_credentials_land_where_the_adapter_reads_them(
        self, ts4k_config, monkeypatch, tmp_path
    ):
        """A source added with config_dir=<custom> must save/check its
        credentials there — sync passes that same config_dir to
        CarddavAdapter, which would otherwise find nothing."""
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": PASSWORD)
        custom_dir = tmp_path / "custom"
        cli._cmd_sources(_args("apple-contacts", [
            "email=a@icloud.com", f"config_dir={custom_dir}",
        ]))

        assert load_credentials("a@icloud.com") is None
        creds = load_credentials("a@icloud.com", custom_dir)
        assert creds is not None and creds["app_password"] == PASSWORD

        assert sources.list_all()["ic"]["config_dir"] == str(custom_dir)

    def test_generic_carddav_setup_prompts_separately_from_an_existing_caldav_credential(
        self, ts4k_config, monkeypatch
    ):
        """A generic (non-iCloud) CardDAV server must not blindly reuse a
        CalDAV credential for the same email — it may be a different
        username/password for an unrelated service. It must prompt and
        store under its own key, leaving the CalDAV credential untouched."""
        from ts4k.auth.caldav import save_credentials

        save_credentials("me@fastmail.com", username="me@fastmail.com",
                         app_password="caldav-pw",
                         server_url="https://caldav.fastmail.com/")
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "carddav-pw")

        cli._cmd_sources(_args("carddav", [
            "email=me@fastmail.com", "server_url=https://carddav.fastmail.com/",
        ]))

        caldav_creds = load_credentials("me@fastmail.com")
        assert caldav_creds["app_password"] == "caldav-pw"
        assert caldav_creds["server_url"] == "https://caldav.fastmail.com/"

        carddav_creds = load_credentials("me@fastmail.com#carddav")
        assert carddav_creds is not None
        assert carddav_creds["app_password"] == "carddav-pw"

    def test_generic_carddav_setup_reuses_its_own_existing_credential(
        self, ts4k_config, monkeypatch
    ):
        """A credential already stored under the service-scoped key is
        reused, same as the iCloud shared-credential case."""
        from ts4k.auth.caldav import save_credentials

        save_credentials("me@fastmail.com#carddav", username="me@fastmail.com",
                         app_password="existing-pw",
                         server_url="")
        monkeypatch.setattr(cli, "_prompt_password", _refuse_prompt)

        cli._cmd_sources(_args("carddav", [
            "email=me@fastmail.com", "server_url=https://carddav.fastmail.com/",
        ]))

        creds = load_credentials("me@fastmail.com#carddav")
        assert creds["app_password"] == "existing-pw"

    def test_generic_carddav_password_whitespace_is_preserved(
        self, ts4k_config, monkeypatch
    ):
        """Unlike Apple app-specific passwords, a generic server's password
        must be stored exactly as entered — whitespace and all."""
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "pass with spaces")
        cli._cmd_sources(_args("carddav", [
            "email=me@fastmail.com", "server_url=https://carddav.fastmail.com/",
        ]))
        creds = load_credentials("me@fastmail.com#carddav")
        assert creds["app_password"] == "pass with spaces"

    def test_icloud_carddav_password_whitespace_is_still_normalized(
        self, ts4k_config, monkeypatch
    ):
        monkeypatch.setattr(cli, "_prompt_password",
                            lambda prompt="": " abcd-efgh-ijkl-mnop\n")
        cli._cmd_sources(_args("apple-contacts", ["email=a@icloud.com"]))
        creds = load_credentials("a@icloud.com")
        assert creds["app_password"] == "abcd-efgh-ijkl-mnop"


class TestContactsSyncParser:
    def _parse(self, argv: list[str]) -> argparse.Namespace:
        return cli._build_parser().parse_args(argv)

    def test_sync_routes_to_the_async_handler(self):
        args = self._parse(["contacts", "sync"])
        assert args.func is cli._cmd_contacts_sync
        assert args.apply is False

    def test_apply_flag(self):
        assert self._parse(["contacts", "sync", "--apply"]).apply is True

    def test_source_flag(self):
        assert self._parse(["c", "sync", "-s", "ic"]).source == "ic"

    def test_other_actions_keep_the_default_handler(self):
        assert self._parse(["contacts", "list"]).func is cli._cmd_contacts


class TestCmdContactsSync:
    async def test_prints_command_output(self, monkeypatch, capsys):
        from unittest.mock import AsyncMock

        monkeypatch.setattr(cli.commands, "sync_contacts",
                            AsyncMock(return_value="plan output"))
        await cli._cmd_contacts_sync(
            argparse.Namespace(source="ic", apply=True)
        )
        assert capsys.readouterr().out.strip() == "plan output"
        cli.commands.sync_contacts.assert_awaited_once_with(source="ic", apply=True)


class TestAuthCarddav:
    def test_explains_the_app_specific_password(self, ts4k_config, capsys):
        sources.add("ic", provider="carddav", email="a@icloud.com")
        cli._cmd_auth(argparse.Namespace(target="ic", check=False, no_calendar=False))
        out = capsys.readouterr().out.lower()
        assert "unknown provider" not in out
        assert "app-specific password" in out
        assert "apple-contacts" in out

    def test_generic_carddav_guidance_names_the_scoped_credential_and_server_url(
        self, ts4k_config, capsys
    ):
        """A generic CardDAV source must not be told to re-run the
        apple-contacts preset (wrong credential, would drop server_url) or
        to delete a plain-email credential path it never wrote to."""
        sources.add("fm", provider="carddav", email="me@fastmail.com",
                     server_url="https://carddav.fastmail.com/")
        cli._cmd_auth(argparse.Namespace(target="fm", check=False, no_calendar=False))
        out = capsys.readouterr().out

        assert "apple-contacts" not in out
        assert "ts4k src add fm carddav email=me@fastmail.com " \
               "server_url=https://carddav.fastmail.com/" in out
        assert str(credentials_path("me@fastmail.com#carddav")) in out
        assert str(credentials_path("me@fastmail.com")) not in out

    def test_icloud_carddav_guidance_is_unchanged(self, ts4k_config, capsys):
        sources.add("ic", provider="carddav", email="a@icloud.com")
        cli._cmd_auth(argparse.Namespace(target="ic", check=False, no_calendar=False))
        out = capsys.readouterr().out

        assert "ts4k src add ic apple-contacts email=a@icloud.com" in out
        assert str(credentials_path("a@icloud.com")) in out

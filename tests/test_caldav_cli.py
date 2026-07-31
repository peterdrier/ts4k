"""Tests for CalDAV source-add CLI flow."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, patch

from ts4k import cli
from ts4k.state import sources


def _args(provider: str, params: list[str]) -> argparse.Namespace:
    return argparse.Namespace(
        action="add", prefix="cc", provider=provider, params=params,
    )


CALS = [
    {"id": "https://caldav.icloud.com/1/calendars/home/", "summary": "Home",
     "access_role": "owner", "timezone": "UTC", "primary": False},
    {"id": "https://caldav.icloud.com/1/calendars/work/", "summary": "Work",
     "access_role": "owner", "timezone": "UTC", "primary": False},
]


class TestSrcAddApple:
    def test_apple_alias_prompts_saves_and_adds_selected(self, ts4k_config, monkeypatch):
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "abcd-efgh-ijkl-mnop")
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(return_value=CALS)):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        from ts4k.auth.caldav import load_credentials
        creds = load_credentials("a@icloud.com")
        assert creds is not None and creds["app_password"] == "abcd-efgh-ijkl-mnop"
        assert creds["server_url"] == "https://caldav.icloud.com"

        cfg = sources.list_all()["cc"]
        assert cfg["provider"] == "caldav"
        assert cfg["calendar_id"] == "https://caldav.icloud.com/1/calendars/home/"
        assert cfg["calendar_name"] == "Home"
        assert cfg["level"] == "readonly"

    def test_explicit_calendar_id_skips_picker(self, ts4k_config, monkeypatch):
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL, save_credentials

        save_credentials("a@icloud.com", username="a@icloud.com",
                         app_password="x", server_url=ICLOUD_CALDAV_URL)
        cli._cmd_sources(_args("apple", [
            "email=a@icloud.com",
            "calendar_id=https://caldav.icloud.com/1/calendars/home/",
            "calendar_name=Home",
        ]))
        cfg = sources.list_all()["cc"]
        assert cfg["provider"] == "caldav"
        assert cfg["calendar_id"] == "https://caldav.icloud.com/1/calendars/home/"

    def test_missing_email_prints_usage_and_adds_nothing(self, ts4k_config, capsys):
        cli._cmd_sources(_args("apple", []))
        assert "email" in capsys.readouterr().out
        assert "cc" not in sources.list_all()


class TestAuthCaldav:
    """`ts4k auth cc` should explain the app-specific password, not call caldav unknown."""

    def _auth_args(self) -> argparse.Namespace:
        return argparse.Namespace(target="cc", check=False, no_calendar=False)

    def test_explains_app_specific_password(self, ts4k_config, capsys):
        sources.add("cc", provider="caldav", email="a@icloud.com",
                    calendar_id="https://caldav.icloud.com/1/calendars/home/")
        cli._cmd_auth(self._auth_args())
        out = capsys.readouterr().out.lower()
        assert "unknown provider" not in out
        assert "app-specific password" in out
        assert "src add" in out

    def test_points_at_the_credential_file_to_replace(self, ts4k_config, capsys):
        """`src add` only prompts when no credential exists — a revoked password
        must be deleted first or the instruction is a dead end."""
        from ts4k.auth.caldav import credentials_path

        sources.add("cc", provider="caldav", email="a@icloud.com",
                    calendar_id="https://caldav.icloud.com/1/calendars/home/")
        cli._cmd_auth(self._auth_args())
        out = capsys.readouterr().out
        assert str(credentials_path("a@icloud.com")) in out
        assert "delete" in out.lower()

    def test_provider_name_resolves(self, ts4k_config, capsys):
        sources.add("cc", provider="caldav", email="a@icloud.com",
                    calendar_id="https://caldav.icloud.com/1/calendars/home/")
        cli._cmd_auth(argparse.Namespace(target="caldav", check=False, no_calendar=False))
        assert "app-specific password" in capsys.readouterr().out.lower()


class TestSuggestPrefix:
    def test_caldav_base_is_cc(self):
        assert cli._suggest_cal_prefix("Home", {}, provider="caldav").startswith("cc")


class TestLocalTimezone:
    def test_uses_tz_env_when_valid(self, monkeypatch):
        monkeypatch.setenv("TZ", "Europe/Amsterdam")
        assert cli._local_timezone() == "Europe/Amsterdam"

    def test_falls_back_to_utc_when_tz_env_invalid(self, monkeypatch):
        monkeypatch.setenv("TZ", "Not/AZone")
        assert cli._local_timezone() != "Not/AZone"


class TestSrcAddTimezone:
    def test_picker_uses_local_timezone(self, ts4k_config, monkeypatch):
        monkeypatch.setenv("TZ", "Europe/Amsterdam")
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "abcd-efgh-ijkl-mnop")
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(return_value=CALS)):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        cfg = sources.list_all()["cc"]
        assert cfg["timezone"] == "Europe/Amsterdam"

    def test_explicit_calendar_id_uses_local_timezone(self, ts4k_config, monkeypatch):
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL, save_credentials

        monkeypatch.setenv("TZ", "Europe/Amsterdam")
        save_credentials("a@icloud.com", username="a@icloud.com",
                         app_password="x", server_url=ICLOUD_CALDAV_URL)
        cli._cmd_sources(_args("apple", [
            "email=a@icloud.com",
            "calendar_id=https://caldav.icloud.com/1/calendars/home/",
            "calendar_name=Home",
        ]))
        cfg = sources.list_all()["cc"]
        assert cfg["timezone"] == "Europe/Amsterdam"


class TestSrcAddUsername:
    def test_explicit_username_is_saved(self, ts4k_config, monkeypatch):
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "abcd-efgh-ijkl-mnop")
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(return_value=CALS)):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com", "username=bob"]))

        from ts4k.auth.caldav import load_credentials
        creds = load_credentials("a@icloud.com")
        assert creds is not None and creds["username"] == "bob"


class TestPromptPassword:
    def test_off_tty_falls_back_to_getpass(self, monkeypatch):
        monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "sentinel-value")
        assert cli._prompt_password("Password: ") == "sentinel-value"


class TestSrcAddPasswordFormat:
    def test_reprompts_on_bad_format_then_accepts_valid(self, ts4k_config, monkeypatch, capsys):
        responses = iter(["wrong-format", "abcd-efgh-ijkl-mnop"])
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": next(responses))
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(return_value=CALS)):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        from ts4k.auth.caldav import load_credentials
        creds = load_credentials("a@icloud.com")
        assert creds is not None and creds["app_password"] == "abcd-efgh-ijkl-mnop"
        assert "app-specific password" in capsys.readouterr().out.lower()

    def test_double_paste_regression_reprompts_then_accepts_valid(self, ts4k_config, monkeypatch):
        double_pasted = "abcd-efgh-ijkl-mnopabcd-efgh-ijkl-mnop"[:39]
        responses = iter([double_pasted, "abcd-efgh-ijkl-mnop"])
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": next(responses))
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(return_value=CALS)):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        from ts4k.auth.caldav import load_credentials
        creds = load_credentials("a@icloud.com")
        assert creds is not None and creds["app_password"] == "abcd-efgh-ijkl-mnop"

    def test_whitespace_is_stripped(self, ts4k_config, monkeypatch):
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": " abcd-efgh-ijkl-mnop\n")
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(return_value=CALS)):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        from ts4k.auth.caldav import load_credentials
        creds = load_credentials("a@icloud.com")
        assert creds is not None and creds["app_password"] == "abcd-efgh-ijkl-mnop"

    def test_three_bad_attempts_aborts(self, ts4k_config, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "nope")
        cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        from ts4k.auth.caldav import load_credentials
        assert load_credentials("a@icloud.com") is None
        assert "aborting" in capsys.readouterr().out.lower()


class TestSrcAddCredentialDiscard:
    def test_fresh_credentials_discarded_on_failed_validation(self, ts4k_config, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_prompt_password", lambda prompt="": "abcd-efgh-ijkl-mnop")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(side_effect=Exception("boom"))):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        from ts4k.auth.caldav import credentials_path
        assert not credentials_path("a@icloud.com").exists()
        assert "cc" not in sources.list_all()
        assert "discarded" in capsys.readouterr().out.lower()

    def test_preexisting_credentials_retained_on_failed_validation(self, ts4k_config, monkeypatch, capsys):
        from ts4k.auth.caldav import ICLOUD_CALDAV_URL, credentials_path, save_credentials

        save_credentials("a@icloud.com", username="a@icloud.com",
                         app_password="abcd-efgh-ijkl-mnop", server_url=ICLOUD_CALDAV_URL)
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(side_effect=Exception("boom"))):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        assert credentials_path("a@icloud.com").exists()
        out = capsys.readouterr().out.lower()
        assert "discarded" not in out

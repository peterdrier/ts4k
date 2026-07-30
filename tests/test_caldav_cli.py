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
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "abcd-efgh")
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(return_value=CALS)):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com"]))

        from ts4k.auth.caldav import load_credentials
        creds = load_credentials("a@icloud.com")
        assert creds is not None and creds["app_password"] == "abcd-efgh"
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
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "abcd-efgh")
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
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "abcd-efgh")
        monkeypatch.setattr("builtins.input", lambda prompt="": "1")
        with patch.object(cli.commands, "cal_list_caldav_calendars",
                          new=AsyncMock(return_value=CALS)):
            cli._cmd_sources(_args("apple", ["email=a@icloud.com", "username=bob"]))

        from ts4k.auth.caldav import load_credentials
        creds = load_credentials("a@icloud.com")
        assert creds is not None and creds["username"] == "bob"

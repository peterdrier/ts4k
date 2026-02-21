"""Tests for ts4k.state.contacts."""

import json

import pytest

from ts4k.state import contacts


@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    """Point contacts at a temp directory for every test."""
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(contacts, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(contacts, "_CONTACTS_FILE", tmp_path / "contacts.json")
    return tmp_path


class TestLink:
    def test_creates_new_alias(self):
        result = contacts.link("sarah", "g:sarah@gmail.com")
        assert result == ["g:sarah@gmail.com"]
        assert contacts.query("sarah") == ["g:sarah@gmail.com"]

    def test_adds_to_existing_alias(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        result = contacts.link("sarah", "w:15551234567@s.whatsapp.net")
        assert result == ["g:sarah@gmail.com", "w:15551234567@s.whatsapp.net"]

    def test_multiple_identifiers_at_once(self):
        result = contacts.link(
            "sarah", "g:sarah@gmail.com", "w:15551234567@s.whatsapp.net"
        )
        assert len(result) == 2

    def test_deduplicates(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        contacts.link("sarah", "g:sarah@gmail.com")
        assert contacts.query("sarah") == ["g:sarah@gmail.com"]

    def test_alias_is_case_insensitive(self):
        contacts.link("Sarah", "g:sarah@gmail.com")
        assert contacts.query("sarah") == ["g:sarah@gmail.com"]

    def test_alias_is_trimmed(self):
        contacts.link("  sarah  ", "g:sarah@gmail.com")
        assert contacts.query("sarah") == ["g:sarah@gmail.com"]

    def test_empty_identifier_ignored(self):
        result = contacts.link("sarah", "", "g:sarah@gmail.com", "  ")
        assert result == ["g:sarah@gmail.com"]


class TestUnlink:
    def test_remove_specific_identifier(self):
        contacts.link("sarah", "g:sarah@gmail.com", "w:12345@s.whatsapp.net")
        result = contacts.unlink("sarah", "g:sarah@gmail.com")
        assert result == ["w:12345@s.whatsapp.net"]

    def test_remove_last_identifier_deletes_alias(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        result = contacts.unlink("sarah", "g:sarah@gmail.com")
        assert result is None
        assert contacts.query("sarah") == []

    def test_remove_entire_alias(self):
        contacts.link("sarah", "g:sarah@gmail.com", "w:12345@s.whatsapp.net")
        result = contacts.unlink("sarah")
        assert result is None
        assert contacts.query("sarah") == []

    def test_remove_nonexistent_alias(self):
        result = contacts.unlink("nobody")
        assert result is None

    def test_remove_nonexistent_identifier(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        result = contacts.unlink("sarah", "w:doesntexist")
        assert result == ["g:sarah@gmail.com"]


class TestQuery:
    def test_returns_identifiers(self):
        contacts.link("sarah", "g:s@g.com", "w:123@wa")
        assert contacts.query("sarah") == ["g:s@g.com", "w:123@wa"]

    def test_returns_empty_for_unknown(self):
        assert contacts.query("nobody") == []


class TestResolve:
    def test_finds_alias_by_identifier(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        assert contacts.resolve("g:sarah@gmail.com") == "sarah"

    def test_returns_none_for_unknown(self):
        assert contacts.resolve("g:unknown@gmail.com") is None

    def test_finds_across_multiple_contacts(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        contacts.link("mom", "w:15559876543@s.whatsapp.net")
        assert contacts.resolve("w:15559876543@s.whatsapp.net") == "mom"


class TestFind:
    def test_find_by_alias_substring(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        contacts.link("sam", "g:sam@gmail.com")
        result = contacts.find("sa")
        assert "sarah" in result
        assert "sam" in result

    def test_find_by_identifier_substring(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        result = contacts.find("gmail.com")
        assert "sarah" in result

    def test_find_case_insensitive(self):
        contacts.link("sarah", "g:Sarah@Gmail.com")
        result = contacts.find("SARAH")
        assert "sarah" in result

    def test_find_no_match(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        assert contacts.find("zzz") == {}


class TestListAll:
    def test_empty_when_no_file(self):
        assert contacts.list_all() == {}

    def test_returns_all_contacts(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        contacts.link("mom", "w:123@wa")
        result = contacts.list_all()
        assert len(result) == 2
        assert "sarah" in result
        assert "mom" in result


class TestClear:
    def test_clear_all(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        contacts.link("mom", "w:123@wa")
        contacts.clear()
        assert contacts.list_all() == {}

    def test_clear_when_empty(self):
        contacts.clear()  # no error


class TestCorruptFile:
    def test_handles_invalid_json(self, tmp_config_dir):
        (tmp_config_dir / "contacts.json").write_text("not json")
        assert contacts.query("sarah") == []
        # Should be able to overwrite corrupt file
        contacts.link("sarah", "g:sarah@gmail.com")
        assert contacts.query("sarah") == ["g:sarah@gmail.com"]

    def test_handles_non_dict_json(self, tmp_config_dir):
        (tmp_config_dir / "contacts.json").write_text('["not", "a", "dict"]')
        assert contacts.list_all() == {}


class TestPersistence:
    def test_survives_reload(self, tmp_config_dir):
        contacts.link("sarah", "g:sarah@gmail.com", "w:123@wa")
        # Read raw file to verify
        data = json.loads((tmp_config_dir / "contacts.json").read_text())
        assert data["sarah"] == ["g:sarah@gmail.com", "w:123@wa"]

    def test_preserves_other_contacts(self):
        contacts.link("sarah", "g:sarah@gmail.com")
        contacts.link("mom", "w:123@wa")
        contacts.unlink("sarah")
        assert contacts.query("mom") == ["w:123@wa"]

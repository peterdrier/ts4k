"""Tests for ts4k.state.sources."""


import pytest

from ts4k.state import sources


@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    """Point sources at a temp directory for every test."""
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
    return tmp_path


class TestAdd:
    def test_creates_source(self):
        entry = sources.add("g", provider="gmail", email="alice@gmail.com")
        assert entry["provider"] == "gmail"
        assert entry["email"] == "alice@gmail.com"
        assert sources.get("g") == entry

    def test_multiple_sources(self):
        sources.add("g", provider="gmail", email="alice@gmail.com")
        sources.add("gn", provider="gmail", email="alice@example.org")
        sources.add("w", provider="whatsapp", mcp_cwd="/path/to/wa")
        assert len(sources.list_all()) == 3

    def test_overwrites_existing(self):
        sources.add("g", provider="gmail", email="old@gmail.com")
        sources.add("g", provider="gmail", email="new@gmail.com")
        assert sources.get("g")["email"] == "new@gmail.com"

    def test_extra_kwargs_stored(self):
        sources.add("g", provider="gmail", email="a@b.com",
                     mcp_url="http://localhost:9999/mcp", transport="stdio")
        cfg = sources.get("g")
        assert cfg["mcp_url"] == "http://localhost:9999/mcp"
        assert cfg["transport"] == "stdio"


class TestGet:
    def test_returns_none_for_missing(self):
        assert sources.get("x") is None

    def test_returns_config(self):
        sources.add("g", provider="gmail", email="a@b.com")
        cfg = sources.get("g")
        assert cfg is not None
        assert cfg["provider"] == "gmail"


class TestRemove:
    def test_removes_existing(self):
        sources.add("g", provider="gmail", email="a@b.com")
        assert sources.remove("g") is True
        assert sources.get("g") is None

    def test_returns_false_for_missing(self):
        assert sources.remove("x") is False

    def test_preserves_others(self):
        sources.add("g", provider="gmail", email="a@b.com")
        sources.add("w", provider="whatsapp", mcp_cwd="/wa")
        sources.remove("g")
        assert sources.get("w") is not None


class TestByProvider:
    def test_filters_by_provider(self):
        sources.add("g", provider="gmail", email="a@b.com")
        sources.add("gn", provider="gmail", email="c@d.com")
        sources.add("w", provider="whatsapp", mcp_cwd="/wa")
        gmail = sources.by_provider("gmail")
        assert len(gmail) == 2
        assert "g" in gmail
        assert "gn" in gmail
        assert "w" not in gmail

    def test_empty_for_unknown(self):
        sources.add("g", provider="gmail", email="a@b.com")
        assert sources.by_provider("telegram") == {}


class TestPrefixes:
    def test_returns_all_prefixes(self):
        sources.add("g", provider="gmail", email="a@b.com")
        sources.add("w", provider="whatsapp", mcp_cwd="/wa")
        assert sorted(sources.prefixes()) == ["g", "w"]


class TestListAll:
    def test_empty_when_no_file(self):
        assert sources.list_all() == {}

    def test_returns_all(self):
        sources.add("g", provider="gmail", email="a@b.com")
        sources.add("w", provider="whatsapp", mcp_cwd="/wa")
        assert len(sources.list_all()) == 2


class TestClear:
    def test_clears_all(self):
        sources.add("g", provider="gmail", email="a@b.com")
        sources.clear()
        assert sources.list_all() == {}


class TestSetNote:
    def test_sets_note_on_existing_source(self):
        sources.add("oh", provider="o365", email="help@burn.camp")
        updated = sources.set_note("oh", "mostly DMARC reports")
        assert updated["note"] == "mostly DMARC reports"
        assert sources.get("oh")["note"] == "mostly DMARC reports"

    def test_preserves_other_fields(self):
        sources.add("oh", provider="o365", email="help@burn.camp", level="modify")
        sources.set_note("oh", "mostly DMARC reports")
        cfg = sources.get("oh")
        assert cfg["provider"] == "o365"
        assert cfg["email"] == "help@burn.camp"
        assert cfg["level"] == "modify"

    def test_empty_note_clears_field(self):
        sources.add("oh", provider="o365", email="help@burn.camp")
        sources.set_note("oh", "mostly DMARC reports")
        sources.set_note("oh", "")
        assert "note" not in sources.get("oh")

    def test_returns_none_for_missing_source(self):
        assert sources.set_note("nope", "text") is None

    def test_updating_note_does_not_touch_other_sources(self):
        sources.add("oh", provider="o365", email="help@burn.camp")
        sources.add("oy", provider="o365", email="peter@oldyard.com")
        sources.set_note("oh", "mostly DMARC reports")
        assert "note" not in sources.get("oy")


class TestCorruptFile:
    def test_handles_invalid_json(self, tmp_config_dir):
        (tmp_config_dir / "sources.json").write_text("not json")
        assert sources.list_all() == {}
        sources.add("g", provider="gmail", email="a@b.com")
        assert sources.get("g") is not None

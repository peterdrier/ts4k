"""Tests for ts4k.state.cache."""

import pytest

# Point cache at a temp directory before importing the module.
@pytest.fixture(autouse=True)
def _tmp_config(tmp_path, monkeypatch):
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
    # Force module to pick up the new path.
    import ts4k.state.cache as mod
    monkeypatch.setattr(mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(mod, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(mod, "_INDEX_FILE", tmp_path / "cache" / "index.json")
    monkeypatch.setattr(mod, "_BODIES_DIR", tmp_path / "cache" / "bodies")


from ts4k.state.cache import (
    store_header, store_body, store_message,
    get_header, get_body, get_message, has,
    list_headers, count, stats, clear,
    CacheBatch,
    SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gmail_msg(id_suffix: str = "abc123", body: str = "Hello world") -> dict:
    return {
        "id": f"g:{id_suffix}",
        "from": "alice@gmail.com",
        "subject": "Test message",
        "date": "2026-02-20T10:00:00Z",
        "source": "g",
        "body": body,
    }


def _o365_msg(id_suffix: str = "def456") -> dict:
    return {
        "id": f"o:{id_suffix}",
        "from": "bob@company.com",
        "subject": "O365 message",
        "date": "2026-02-21T14:00:00Z",
        "source": "o",
        "body": "Office content",
    }


# Providers passed to store_header/store_message/CacheBatch.store_header —
# caching gates on this caller-supplied provider, not the "source" prefix
# in the header dict (see issue #64).
GMAIL = "gmail"
O365 = "o365"


# ---------------------------------------------------------------------------
# Store & retrieve
# ---------------------------------------------------------------------------

class TestStoreRetrieve:
    def test_store_and_get_header(self):
        msg = _gmail_msg()
        store_header("g:abc123", msg, provider=GMAIL)
        hdr = get_header("g:abc123")
        assert hdr is not None
        assert hdr["from"] == "alice@gmail.com"
        assert "body" not in hdr  # body stripped from header

    def test_store_and_get_body(self):
        store_body("g:abc123", "Hello world")
        assert get_body("g:abc123") == "Hello world"

    def test_store_message_stores_both(self):
        msg = _gmail_msg()
        store_message("g:abc123", msg, provider=GMAIL)
        assert get_header("g:abc123") is not None
        assert get_body("g:abc123") == "Hello world"

    def test_get_message_merges_header_and_body(self):
        store_message("g:abc123", _gmail_msg(), provider=GMAIL)
        full = get_message("g:abc123")
        assert full is not None
        assert full["from"] == "alice@gmail.com"
        assert full["body"] == "Hello world"

    def test_get_message_header_only(self):
        """get_message returns header without body if body not cached."""
        store_header("g:abc123", _gmail_msg(), provider=GMAIL)
        full = get_message("g:abc123")
        assert full is not None
        assert full["from"] == "alice@gmail.com"
        assert "body" not in full  # no body file exists

    def test_has(self):
        assert not has("g:abc123")
        store_header("g:abc123", _gmail_msg(), provider=GMAIL)
        assert has("g:abc123")

    def test_miss_returns_none(self):
        assert get_header("g:nonexistent") is None
        assert get_body("g:nonexistent") is None
        assert get_message("g:nonexistent") is None


# ---------------------------------------------------------------------------
# Cacheable providers
# ---------------------------------------------------------------------------

class TestCacheableProviders:
    def test_gmail_cached(self):
        store_message("g:abc", _gmail_msg(), provider=GMAIL)
        assert has("g:abc")

    def test_o365_cached(self):
        store_message("o:def", _o365_msg(), provider=O365)
        assert has("o:def")

    def test_whatsapp_not_cached(self):
        msg = {"id": "w:123", "from": "alice", "subject": "", "date": "2026-01-01", "source": "w", "body": "hi"}
        store_message("w:123", msg, provider="whatsapp")
        assert not has("w:123")  # silently skipped

    def test_no_provider_not_cached(self):
        """Omitting provider is a no-op — cache.py never guesses from source."""
        store_message("g:noprov", _gmail_msg("noprov"))
        assert not has("g:noprov")

    def test_gmail_source_registered_under_custom_prefix_is_cached(self):
        """Issue #64: prefixes are user-chosen, so caching must gate on
        provider, not on the literal "g"/"o" prefix string."""
        msg = {**_gmail_msg("custom"), "id": "gw:custom", "source": "gw"}
        store_message("gw:custom", msg, provider=GMAIL)
        assert has("gw:custom")

    def test_o365_source_registered_under_custom_prefix_is_cached(self):
        msg = {**_o365_msg("custom"), "id": "oy:custom", "source": "oy"}
        store_message("oy:custom", msg, provider=O365)
        assert has("oy:custom")


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------

class TestSchemaVersioning:
    def test_current_version_is_hit(self):
        store_message("g:abc", _gmail_msg(), provider=GMAIL)
        assert get_header("g:abc") is not None

    def test_old_version_is_miss(self, monkeypatch):
        store_message("g:abc", _gmail_msg(), provider=GMAIL)
        # Bump schema — existing entry is now stale
        import ts4k.state.cache as mod
        monkeypatch.setattr(mod, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
        assert get_header("g:abc") is None
        assert not has("g:abc")

    def test_stale_entry_in_stats(self, monkeypatch):
        store_message("g:abc", _gmail_msg(), provider=GMAIL)
        import ts4k.state.cache as mod
        monkeypatch.setattr(mod, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
        s = stats()
        assert s["stale"] == 1
        assert s["total"] == 0


# ---------------------------------------------------------------------------
# Query / listing
# ---------------------------------------------------------------------------

class TestListHeaders:
    def test_list_all(self):
        store_message("g:a", _gmail_msg("a"), provider=GMAIL)
        store_message("o:b", _o365_msg("b"), provider=O365)
        results = list_headers()
        assert len(results) == 2

    def test_filter_by_source(self):
        store_message("g:a", _gmail_msg("a"), provider=GMAIL)
        store_message("o:b", _o365_msg("b"), provider=O365)
        results = list_headers(source="g")
        assert len(results) == 1
        assert results[0]["source"] == "g"

    def test_filter_by_contact(self):
        store_message("g:a", _gmail_msg("a"), provider=GMAIL)
        store_message("o:b", _o365_msg("b"), provider=O365)
        results = list_headers(contact="alice")
        assert len(results) == 1
        assert "alice" in results[0]["from"]

    def test_filter_by_since(self):
        store_message("g:a", _gmail_msg("a"), provider=GMAIL)  # date 2026-02-20
        store_message("o:b", _o365_msg("b"), provider=O365)  # date 2026-02-21
        results = list_headers(since="2026-02-21T00:00:00Z")
        assert len(results) == 1
        assert results[0]["source"] == "o"

    def test_count(self):
        store_message("g:a", _gmail_msg("a"), provider=GMAIL)
        store_message("g:b", _gmail_msg("b"), provider=GMAIL)
        store_message("o:c", _o365_msg("c"), provider=O365)
        assert count() == 3
        assert count(source="g") == 2
        assert count(source="o") == 1


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_empty_stats(self):
        s = stats()
        assert s["total"] == 0
        assert s["stale"] == 0
        assert s["bodies"] == 0

    def test_stats_with_data(self):
        store_message("g:a", _gmail_msg("a"), provider=GMAIL)
        store_message("o:b", _o365_msg("b"), provider=O365)
        s = stats()
        assert s["total"] == 2
        assert s["by_source"]["g"] == 1
        assert s["by_source"]["o"] == 1
        assert s["bodies"] == 2
        assert s["index_bytes"] > 0
        assert s["bodies_bytes"] > 0
        assert s["oldest"] == "2026-02-20T10:00:00Z"
        assert s["newest"] == "2026-02-21T14:00:00Z"
        assert s["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_all(self):
        store_message("g:a", _gmail_msg("a"), provider=GMAIL)
        store_message("o:b", _o365_msg("b"), provider=O365)
        removed = clear()
        assert removed == 2
        assert count() == 0
        assert get_body("g:a") is None  # body files deleted too

    def test_clear_by_source(self):
        store_message("g:a", _gmail_msg("a"), provider=GMAIL)
        store_message("o:b", _o365_msg("b"), provider=O365)
        removed = clear(source="g")
        assert removed == 1
        assert not has("g:a")
        assert has("o:b")

    def test_clear_stale_only(self, monkeypatch):
        store_message("g:a", _gmail_msg("a"), provider=GMAIL)
        store_message("g:b", _gmail_msg("b"), provider=GMAIL)
        import ts4k.state.cache as mod
        monkeypatch.setattr(mod, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
        # Store one more under new schema
        store_message("g:c", _gmail_msg("c"), provider=GMAIL)
        removed = clear(stale_only=True)
        assert removed == 2  # a and b are stale
        assert has("g:c")    # c is current

    def test_clear_returns_zero_when_empty(self):
        assert clear() == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_corrupt_index_recovers(self, tmp_path):
        """Corrupt index.json is treated as empty."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "index.json").write_text("NOT JSON", encoding="utf-8")
        assert count() == 0

    def test_corrupt_body_returns_none(self, tmp_path):
        store_header("g:abc", _gmail_msg(), provider=GMAIL)
        bodies_dir = tmp_path / "cache" / "bodies"
        bodies_dir.mkdir(parents=True, exist_ok=True)
        (bodies_dir / "g_abc.json").write_text("NOT JSON", encoding="utf-8")
        assert get_body("g:abc") is None

    def test_overwrite_existing_entry(self):
        store_message("g:abc", _gmail_msg(body="version 1"), provider=GMAIL)
        store_message("g:abc", _gmail_msg(body="version 2"), provider=GMAIL)
        assert get_body("g:abc") == "version 2"

    def test_cached_at_timestamp(self):
        store_header("g:abc", _gmail_msg(), provider=GMAIL)
        hdr = get_header("g:abc")
        assert "_cached_at" in hdr


# ---------------------------------------------------------------------------
# CacheBatch
# ---------------------------------------------------------------------------

class TestCacheBatch:
    def test_stores_multiple_headers(self):
        with CacheBatch() as cb:
            cb.store_header("g:a", _gmail_msg("a"), provider=GMAIL)
            cb.store_header("g:b", _gmail_msg("b"), provider=GMAIL)
        assert has("g:a")
        assert has("g:b")

    def test_writes_index_once(self, monkeypatch):
        """Batch should call _save_index exactly once on exit."""
        import ts4k.state.cache as mod
        call_count = 0
        original = mod._save_index

        def counting_save(data):
            nonlocal call_count
            call_count += 1
            original(data)

        monkeypatch.setattr(mod, "_save_index", counting_save)

        with CacheBatch() as cb:
            cb.store_header("g:a", _gmail_msg("a"), provider=GMAIL)
            cb.store_header("g:b", _gmail_msg("b"), provider=GMAIL)
            cb.store_header("g:c", _gmail_msg("c"), provider=GMAIL)
            assert call_count == 0  # nothing written yet

        assert call_count == 1  # single write on exit

    def test_flushes_on_exception(self):
        """Data accumulated before an exception should be persisted."""
        try:
            with CacheBatch() as cb:
                cb.store_header("g:saved", _gmail_msg("saved"), provider=GMAIL)
                raise ValueError("boom")
        except ValueError:
            pass
        assert has("g:saved")

    def test_explicit_flush(self, monkeypatch):
        import ts4k.state.cache as mod
        call_count = 0
        original = mod._save_index

        def counting_save(data):
            nonlocal call_count
            call_count += 1
            original(data)

        monkeypatch.setattr(mod, "_save_index", counting_save)

        with CacheBatch() as cb:
            cb.store_header("g:a", _gmail_msg("a"), provider=GMAIL)
            cb.flush()
            assert call_count == 1
            cb.store_header("g:b", _gmail_msg("b"), provider=GMAIL)

        assert call_count == 2  # flush + exit

    def test_skips_non_cacheable(self):
        """WhatsApp messages should be silently skipped."""
        wa_msg = {"id": "w:1", "from": "x", "subject": "", "date": "2026-01-01", "source": "w"}
        with CacheBatch() as cb:
            cb.store_header("w:1", wa_msg, provider="whatsapp")
        assert not has("w:1")

    def test_stores_non_canonical_prefix_when_provider_cacheable(self):
        """Issue #64: CacheBatch gates on provider too, not on prefix."""
        msg = {**_gmail_msg("batch"), "id": "gw:batch", "source": "gw"}
        with CacheBatch() as cb:
            cb.store_header("gw:batch", msg, provider=GMAIL)
        assert has("gw:batch")

    def test_raises_outside_context(self):
        cb = CacheBatch()
        with pytest.raises(RuntimeError, match="context manager"):
            cb.store_header("g:a", _gmail_msg("a"), provider=GMAIL)

    def test_flush_raises_outside_context(self):
        cb = CacheBatch()
        with pytest.raises(RuntimeError, match="context manager"):
            cb.flush()

    def test_no_write_when_empty(self, monkeypatch):
        """An empty batch should not write to disk."""
        import ts4k.state.cache as mod
        call_count = 0
        original = mod._save_index

        def counting_save(data):
            nonlocal call_count
            call_count += 1
            original(data)

        monkeypatch.setattr(mod, "_save_index", counting_save)

        with CacheBatch():
            pass

        assert call_count == 0

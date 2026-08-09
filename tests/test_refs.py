"""Tests for the RefTable — short reference mapping (#N → full ID)."""

from __future__ import annotations



from ts4k.state.refs import RefTable


# ---------------------------------------------------------------------------
# Basic assign / resolve
# ---------------------------------------------------------------------------


class TestRefTableBasic:
    def test_assign_sequential(self):
        rt = RefTable()
        msgs = [
            {"id": "g:abc123"},
            {"id": "o:AAMkAD...long"},
            {"id": "w:3EB05C"},
        ]
        ref_map = rt.assign(msgs)
        assert ref_map == {"g:abc123": 1, "o:AAMkAD...long": 2, "w:3EB05C": 3}

    def test_resolve_valid(self):
        rt = RefTable()
        rt.assign([{"id": "g:abc123"}, {"id": "o:xyz"}])
        assert rt.resolve("#1") == "g:abc123"
        assert rt.resolve("#2") == "o:xyz"

    def test_resolve_bare_number(self):
        rt = RefTable()
        rt.assign([{"id": "g:abc123"}, {"id": "o:xyz"}])
        assert rt.resolve("1") == "g:abc123"
        assert rt.resolve("2") == "o:xyz"

    def test_resolve_not_found(self):
        rt = RefTable()
        rt.assign([{"id": "g:abc"}])
        assert rt.resolve("#99") is None

    def test_resolve_invalid_format(self):
        rt = RefTable()
        assert rt.resolve("abc") is None
        assert rt.resolve("#") is None
        assert rt.resolve("") is None

    def test_resolve_with_whitespace(self):
        rt = RefTable()
        rt.assign([{"id": "g:abc"}])
        assert rt.resolve(" #1 ") == "g:abc"

    def test_empty_id_skipped(self):
        rt = RefTable()
        ref_map = rt.assign([{"id": ""}, {"id": "g:abc"}])
        assert ref_map == {"g:abc": 1}

    def test_missing_id_skipped(self):
        rt = RefTable()
        ref_map = rt.assign([{"from": "x@y.com"}, {"id": "g:abc"}])
        assert ref_map == {"g:abc": 1}


# ---------------------------------------------------------------------------
# Accumulate mode (MCP)
# ---------------------------------------------------------------------------


class TestRefTableAccumulate:
    def test_accumulate_across_calls(self):
        rt = RefTable()
        rt.assign([{"id": "g:1"}, {"id": "g:2"}])
        ref_map = rt.assign([{"id": "g:3"}, {"id": "g:4"}])
        assert ref_map == {"g:3": 3, "g:4": 4}
        assert rt.resolve("#1") == "g:1"
        assert rt.resolve("#4") == "g:4"

    def test_reuses_existing_ref(self):
        rt = RefTable()
        rt.assign([{"id": "g:1"}, {"id": "g:2"}])
        # Second call includes g:1 again — should keep ref #1
        ref_map = rt.assign([{"id": "g:1"}, {"id": "g:3"}])
        assert ref_map["g:1"] == 1
        assert ref_map["g:3"] == 3


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------


class TestRefTableClear:
    def test_clear_resets(self):
        rt = RefTable()
        rt.assign([{"id": "g:1"}, {"id": "g:2"}])
        rt.clear()
        assert rt.resolve("#1") is None
        # New assignments start from 1
        ref_map = rt.assign([{"id": "g:new"}])
        assert ref_map == {"g:new": 1}


# ---------------------------------------------------------------------------
# Persistence (CLI mode)
# ---------------------------------------------------------------------------


class TestRefTablePersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "refs.json"
        rt = RefTable()
        rt.assign([{"id": "g:aaa"}, {"id": "o:bbb"}])
        rt.save(path)

        rt2 = RefTable()
        rt2.load(path)
        assert rt2.resolve("#1") == "g:aaa"
        assert rt2.resolve("#2") == "o:bbb"
        # Next assignment continues from 3
        ref_map = rt2.assign([{"id": "w:ccc"}])
        assert ref_map == {"w:ccc": 3}

    def test_load_missing_file(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        rt = RefTable()
        rt.load(path)  # should not raise
        assert rt.resolve("#1") is None

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json at all", encoding="utf-8")
        rt = RefTable()
        rt.load(path)  # should not raise
        assert rt.resolve("#1") is None

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "refs.json"
        rt = RefTable()
        rt.assign([{"id": "g:x"}])
        rt.save(path)
        assert path.exists()

    def test_load_preserves_reverse_map(self, tmp_path):
        """Loaded table should still deduplicate on re-assign."""
        path = tmp_path / "refs.json"
        rt = RefTable()
        rt.assign([{"id": "g:aaa"}])
        rt.save(path)

        rt2 = RefTable()
        rt2.load(path)
        # Re-assigning same ID should reuse ref #1
        ref_map = rt2.assign([{"id": "g:aaa"}])
        assert ref_map == {"g:aaa": 1}

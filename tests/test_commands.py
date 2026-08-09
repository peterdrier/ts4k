"""Tests for the ts4k command router (commands.py).

Verifies that command functions return strings (not print), handle contacts,
filters, and status without needing real adapters.
"""

from __future__ import annotations



from ts4k import commands


# ---------------------------------------------------------------------------
# manage_contacts
# ---------------------------------------------------------------------------


class TestManageContacts:
    def test_link_and_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
        # Force reload of module-level paths
        from ts4k.state import contacts as c
        monkeypatch.setattr(c, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(c, "_CONTACTS_FILE", tmp_path / "contacts.json")

        out = commands.manage_contacts(
            action="link", alias="alice", identifiers=["g:alice@test.com", "w:123"]
        )
        assert "alice" in out
        assert "g:alice@test.com" in out

        out = commands.manage_contacts(action="list")
        assert "alice" in out

    def test_find(self, tmp_path, monkeypatch):
        from ts4k.state import contacts as c
        monkeypatch.setattr(c, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(c, "_CONTACTS_FILE", tmp_path / "contacts.json")

        commands.manage_contacts(
            action="link", alias="bob", identifiers=["g:bob@test.com"]
        )
        out = commands.manage_contacts(action="find", term="bob")
        assert "bob" in out

        out = commands.manage_contacts(action="find", term="nonexistent")
        assert "No matches" in out

    def test_unlink(self, tmp_path, monkeypatch):
        from ts4k.state import contacts as c
        monkeypatch.setattr(c, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(c, "_CONTACTS_FILE", tmp_path / "contacts.json")

        commands.manage_contacts(
            action="link", alias="carol", identifiers=["g:carol@test.com"]
        )
        out = commands.manage_contacts(action="unlink", alias="carol")
        assert "(removed)" in out

    def test_link_missing_alias(self):
        out = commands.manage_contacts(action="link", alias=None, identifiers=["x"])
        assert "Error" in out

    def test_link_missing_identifiers(self):
        out = commands.manage_contacts(action="link", alias="x", identifiers=None)
        assert "Error" in out


# ---------------------------------------------------------------------------
# manage_filters
# ---------------------------------------------------------------------------


class TestManageFilters:
    def test_show_default(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        out = commands.manage_filters(action="show")
        assert "skip_senders" in out
        assert "(none)" in out

    def test_add_and_remove_sender(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        out = commands.manage_filters(action="add-sender", value="spam@test.com")
        assert "spam@test.com" in out

        out = commands.manage_filters(action="rm-sender", value="spam@test.com")
        assert "spam@test.com" not in out or "(empty)" in out

    def test_add_domain(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        out = commands.manage_filters(action="add-domain", value="junk.com")
        assert "junk.com" in out

    def test_reset(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        commands.manage_filters(action="add-sender", value="x@y.com")
        out = commands.manage_filters(action="reset")
        assert "reset" in out.lower()

    def test_skip_groups(self, tmp_path, monkeypatch):
        from ts4k.state import filters as f
        monkeypatch.setattr(f, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(f, "_FILTERS_FILE", tmp_path / "filters.json")

        out = commands.manage_filters(action="skip-groups", value="true")
        assert "True" in out


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_status_no_data(self, tmp_path, monkeypatch):
        """Status with no sources or stats should not crash."""
        from ts4k import state

        state.set_config_dir(tmp_path, reason="test")

        out = commands.get_status()
        assert "Sources:" in out
        assert "Contacts:" in out
        assert "Filters:" in out
        assert "Stats:" in out
        assert str(tmp_path) in out
        assert "(test)" in out
        assert isinstance(out, str)

        state.reset()


# ---------------------------------------------------------------------------
# CommandResult
# ---------------------------------------------------------------------------


class TestCommandResult:
    def test_default_values(self):
        r = commands.CommandResult()
        assert r.output == ""
        assert r.messages_processed == 0
        assert r.error is None
        assert r.ref_map is None

    def test_with_error(self):
        r = commands.CommandResult(error="bad thing")
        assert r.error == "bad thing"
        assert r.output == ""

    def test_with_ref_map(self):
        r = commands.CommandResult(ref_map={"g:abc": 1})
        assert r.ref_map == {"g:abc": 1}


# ---------------------------------------------------------------------------
# _resolve_ref
# ---------------------------------------------------------------------------


class TestResolveRef:
    def test_passthrough_real_id(self):
        from ts4k.state.refs import RefTable
        rt = RefTable()
        rt.assign([{"id": "g:abc"}])
        assert commands._resolve_ref("g:abc", rt) == "g:abc"

    def test_resolve_ref(self):
        from ts4k.state.refs import RefTable
        rt = RefTable()
        rt.assign([{"id": "g:abc"}])
        assert commands._resolve_ref("#1", rt) == "g:abc"

    def test_unresolvable_ref_passes_through(self):
        from ts4k.state.refs import RefTable
        rt = RefTable()
        assert commands._resolve_ref("#99", rt) == "#99"

    def test_resolve_bare_number(self):
        from ts4k.state.refs import RefTable
        rt = RefTable()
        rt.assign([{"id": "g:abc"}])
        assert commands._resolve_ref("1", rt) == "g:abc"

    def test_no_ref_table(self):
        assert commands._resolve_ref("#1", None) == "#1"
        assert commands._resolve_ref("1", None) == "1"
        assert commands._resolve_ref("g:abc", None) == "g:abc"


class TestSkillReference:
    """Skill text is the agent's only self-documentation — pin what it must say."""

    def test_voice_notes_are_declared_transcribed(self):
        """Agents must not ask the user to listen to audio (ts4k#48).

        The bridge folds the transcript into the message body; nothing in the
        response shape says so, so the skill text is where an agent learns it.
        """
        text = commands.skill_reference("basic")
        assert "[voice" in text
        assert "transcript" in text.lower()

    def test_voice_guidance_survives_in_one_line(self):
        """It rides in every skill call — one line is the budget."""
        lines = [ln for ln in commands.skill_reference("basic").splitlines() if "[voice" in ln]
        assert len(lines) == 1, lines

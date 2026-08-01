"""Tests for ts4k filter config (state.filters) and engine (core.filter)."""

import json

import pytest

from ts4k.core.filter import apply_filters
from ts4k.state import filters


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    """Point filters at a temp directory for every test."""
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(filters, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(filters, "_FILTERS_FILE", tmp_path / "filters.json")
    return tmp_path


def _msg(
    sender: str = "alice@example.com",
    subject: str = "Hello",
    body: str = "Hi there",
    is_group: bool = False,
    chat_jid: str = "",
) -> dict:
    """Build a minimal message dict for testing."""
    d = {
        "id": "test:1",
        "from": sender,
        "subject": subject,
        "body": body,
        "date": "2026-02-21T12:00:00Z",
    }
    if is_group:
        d["is_group"] = True
    if chat_jid:
        d["chat_jid"] = chat_jid
    return d


# ---------------------------------------------------------------------------
# State: filters config management
# ---------------------------------------------------------------------------


class TestFilterConfig:
    def test_defaults_when_no_file(self):
        config = filters.get_config()
        assert config["skip_senders"] == []
        assert config["skip_domains"] == []
        assert config["skip_groups"] is False
        assert config["skip_patterns"] == []

    def test_add_sender(self):
        result = filters.add_sender("noreply@linkedin.com")
        assert "noreply@linkedin.com" in result
        assert "noreply@linkedin.com" in filters.get_config()["skip_senders"]

    def test_add_sender_deduplicates(self):
        filters.add_sender("noreply@linkedin.com")
        filters.add_sender("noreply@linkedin.com")
        assert filters.get_config()["skip_senders"].count("noreply@linkedin.com") == 1

    def test_add_sender_lowercases(self):
        filters.add_sender("NoReply@LinkedIn.com")
        assert "noreply@linkedin.com" in filters.get_config()["skip_senders"]

    def test_remove_sender(self):
        filters.add_sender("noreply@linkedin.com")
        result = filters.remove_sender("noreply@linkedin.com")
        assert "noreply@linkedin.com" not in result

    def test_add_domain(self):
        result = filters.add_domain("marketing.example.com")
        assert "marketing.example.com" in result

    def test_remove_domain(self):
        filters.add_domain("marketing.example.com")
        result = filters.remove_domain("marketing.example.com")
        assert "marketing.example.com" not in result

    def test_add_pattern(self):
        result = filters.add_pattern(r"unsubscribe.*click")
        assert r"unsubscribe.*click" in result

    def test_remove_pattern(self):
        filters.add_pattern(r"unsubscribe.*click")
        result = filters.remove_pattern(r"unsubscribe.*click")
        assert r"unsubscribe.*click" not in result

    def test_set_skip_groups(self):
        assert filters.set_skip_groups(True) is True
        assert filters.get_config()["skip_groups"] is True
        assert filters.set_skip_groups(False) is False

    def test_set_config_replaces_all(self):
        filters.add_sender("old@example.com")
        filters.set_config({"skip_senders": ["new@example.com"]})
        config = filters.get_config()
        assert config["skip_senders"] == ["new@example.com"]
        # Defaults still present for unset fields
        assert config["skip_domains"] == []

    def test_reset(self):
        filters.add_sender("noreply@linkedin.com")
        filters.add_domain("marketing.example.com")
        result = filters.reset()
        assert result["skip_senders"] == []
        assert result["skip_domains"] == []

    def test_corrupt_file(self, tmp_config_dir):
        (tmp_config_dir / "filters.json").write_text("not json")
        config = filters.get_config()
        assert config["skip_senders"] == []  # falls back to defaults

    def test_preserves_unknown_fields(self):
        filters.set_config({"skip_senders": [], "custom_field": "value"})
        config = filters.get_config()
        assert config.get("custom_field") == "value"


# ---------------------------------------------------------------------------
# Engine: apply_filters
# ---------------------------------------------------------------------------


class TestSkipSenders:
    def test_skips_exact_sender(self):
        config = {"skip_senders": ["noreply@linkedin.com"]}
        msgs = [_msg(sender="noreply@linkedin.com"), _msg(sender="alice@example.com")]
        result = apply_filters(msgs, config)
        assert len(result) == 1
        assert result[0]["from"] == "alice@example.com"

    def test_case_insensitive(self):
        config = {"skip_senders": ["noreply@linkedin.com"]}
        msgs = [_msg(sender="NoReply@LinkedIn.com")]
        assert len(apply_filters(msgs, config)) == 0

    def test_empty_skip_list_keeps_all(self):
        config = {"skip_senders": []}
        msgs = [_msg(), _msg()]
        assert len(apply_filters(msgs, config)) == 2


class TestSkipDomains:
    def test_skips_by_domain(self):
        config = {"skip_domains": ["marketing.example.com"]}
        msgs = [
            _msg(sender="promo@marketing.example.com"),
            _msg(sender="alice@example.com"),
        ]
        result = apply_filters(msgs, config)
        assert len(result) == 1
        assert result[0]["from"] == "alice@example.com"

    def test_case_insensitive(self):
        config = {"skip_domains": ["marketing.example.com"]}
        msgs = [_msg(sender="promo@Marketing.Example.COM")]
        assert len(apply_filters(msgs, config)) == 0

    def test_no_domain_in_sender(self):
        config = {"skip_domains": ["example.com"]}
        msgs = [_msg(sender="just-a-name")]
        assert len(apply_filters(msgs, config)) == 1


class TestSkipGroups:
    def test_skips_group_by_flag(self):
        config = {"skip_groups": True}
        msgs = [_msg(is_group=True), _msg(is_group=False)]
        result = apply_filters(msgs, config)
        assert len(result) == 1

    def test_skips_group_by_jid(self):
        config = {"skip_groups": True}
        msgs = [
            _msg(chat_jid="120363123456@g.us"),
            _msg(chat_jid="15551234567@s.whatsapp.net"),
        ]
        result = apply_filters(msgs, config)
        assert len(result) == 1
        assert result[0]["chat_jid"] == "15551234567@s.whatsapp.net"

    def test_skip_groups_false_keeps_all(self):
        config = {"skip_groups": False}
        msgs = [_msg(is_group=True), _msg(is_group=False)]
        assert len(apply_filters(msgs, config)) == 2


class TestSkipPatterns:
    def test_skips_by_subject_pattern(self):
        config = {"skip_patterns": [r"daily\s+digest"]}
        msgs = [
            _msg(subject="Daily Digest for Feb 21"),
            _msg(subject="Meeting tomorrow"),
        ]
        result = apply_filters(msgs, config)
        assert len(result) == 1
        assert result[0]["subject"] == "Meeting tomorrow"

    def test_skips_by_body_pattern(self):
        config = {"skip_patterns": [r"unsubscribe.*click here"]}
        msgs = [
            _msg(body="To unsubscribe, click here and confirm."),
            _msg(body="Hey, let's meet for coffee."),
        ]
        result = apply_filters(msgs, config)
        assert len(result) == 1

    def test_invalid_regex_silently_ignored(self):
        config = {"skip_patterns": [r"[invalid", r"valid"]}
        msgs = [_msg(subject="valid match"), _msg(subject="no match")]
        result = apply_filters(msgs, config)
        assert len(result) == 1

    def test_case_insensitive(self):
        config = {"skip_patterns": [r"newsletter"]}
        msgs = [_msg(subject="WEEKLY NEWSLETTER")]
        assert len(apply_filters(msgs, config)) == 0


class TestCombinedFilters:
    def test_any_rule_skips(self):
        """A message is skipped if ANY rule matches."""
        config = {
            "skip_senders": ["spam@example.com"],
            "skip_domains": ["marketing.co"],
            "skip_groups": True,
            "skip_patterns": [r"lottery"],
        }
        msgs = [
            _msg(sender="spam@example.com"),               # sender match
            _msg(sender="promo@marketing.co"),              # domain match
            _msg(is_group=True),                            # group match
            _msg(subject="You won the lottery!"),           # pattern match
            _msg(sender="friend@real.com", subject="Hi"),   # no match
        ]
        result = apply_filters(msgs, config)
        assert len(result) == 1
        assert result[0]["from"] == "friend@real.com"

    def test_empty_config_keeps_all(self):
        msgs = [_msg(), _msg(), _msg()]
        assert len(apply_filters(msgs, {})) == 3

    def test_missing_fields_in_message(self):
        """Messages with missing from/subject/body don't crash."""
        config = {
            "skip_senders": ["x@y.com"],
            "skip_patterns": ["test"],
        }
        msgs = [{"id": "1", "date": "2026-01-01"}]
        result = apply_filters(msgs, config)
        assert len(result) == 1

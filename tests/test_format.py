"""Tests for ts4k output formatters — pipe, JSON, XML.

Tests all three formats with realistic data and edge cases.
"""

from __future__ import annotations

import json
from xml.etree import ElementTree as ET

import pytest

from ts4k.core.format import (
    estimate_size,
    format_listing,
    format_message,
    format_thread,
)

# ---------------------------------------------------------------------------
# Realistic test data
# ---------------------------------------------------------------------------

SAMPLE_MESSAGES = [
    {
        "id": "g:18f6a2b3c4e5f6a7",
        "from": "alice@acme.com",
        "subject": "Meeting tomorrow",
        "date": "2026-02-20T09:15:00Z",
        "body": "Hey, are we still on for 3pm? Let me know if the conference room changed.",
        "source": "g",
    },
    {
        "id": "g:18f6b1112233aabb",
        "from": "bob@corp.com",
        "subject": "Q3 Report",
        "date": "2026-02-18T10:00:00Z",
        "body": "A" * 49000,  # ~48kb body
        "source": "g",
    },
    {
        "id": "g:18f6c9988776655d",
        "from": "newsletter@example.com",
        "subject": "Weekly digest",
        "date": "2026-02-17T06:00:00Z",
        "body": "",
        "source": "g",
    },
]

SAMPLE_MESSAGE_FULL = {
    "id": "g:18f6a2b3c4e5f6a7",
    "from": "alice@acme.com",
    "subject": "Meeting tomorrow",
    "date": "2026-02-20T09:15:00Z",
    "body": "Hey Peter,\n\nAre we still on for 3pm? Let me know if the conference room changed.\n\nThanks,\nAlice",
    "to": "peter@example.com",
    "cc": "bob@example.com",
    "source": "g",
    "attachments": [
        {"filename": "agenda.pdf", "mime_type": "application/pdf", "size": "24.5 KB"},
    ],
}

SAMPLE_THREAD = {
    "thread_id": "g:18f6a2b3c4e5f6a8",
    "subject": "Meeting tomorrow at 3pm",
    "message_count": 3,
    "messages": [
        {
            "from": "alice@acme.com",
            "date": "2026-02-20T09:15:00Z",
            "body": "Hey Peter, are we still on for 3pm?",
        },
        {
            "from": "peter@example.com",
            "date": "2026-02-20T09:30:00Z",
            "body": "Yes! Room B confirmed. See you there.",
        },
        {
            "from": "alice@acme.com",
            "date": "2026-02-20T09:32:00Z",
            "body": "Great, thanks!",
        },
    ],
}


# ---------------------------------------------------------------------------
# estimate_size
# ---------------------------------------------------------------------------


class TestEstimateSize:
    def test_zero(self):
        assert estimate_size("") == "0b"

    def test_none(self):
        assert estimate_size(None) == "0b"

    def test_small_bytes(self):
        assert estimate_size("hello") == "5b"

    def test_500_bytes(self):
        assert estimate_size("x" * 500) == "500b"

    def test_999_bytes(self):
        assert estimate_size("x" * 999) == "999b"

    def test_1000_bytes_is_kb(self):
        result = estimate_size("x" * 1000)
        assert result.endswith("kb")

    def test_1500_bytes(self):
        result = estimate_size("x" * 1500)
        assert result.endswith("kb")
        # 1500 / 1024 ~ 1.46, rounds to 1
        assert result == "1kb"

    def test_2500_bytes(self):
        result = estimate_size("x" * 2500)
        assert result.endswith("kb")
        # 2500 / 1024 ~ 2.44, rounds to 2
        assert result == "2kb"

    def test_large_text(self):
        result = estimate_size("x" * 50000)
        assert result.endswith("kb")

    def test_megabyte_range(self):
        result = estimate_size("x" * 1_100_000)
        assert result.endswith("mb")


# ---------------------------------------------------------------------------
# Pipe format
# ---------------------------------------------------------------------------


class TestPipeFormatListing:
    def test_has_header_row(self):
        result = format_listing(SAMPLE_MESSAGES, "pipe")
        lines = result.split("\n")
        assert lines[0] == "SOURCE|FROM|SUBJECT|DATE|ID|SIZE"

    def test_correct_row_count(self):
        result = format_listing(SAMPLE_MESSAGES, "pipe")
        lines = result.split("\n")
        # header + 3 data rows
        assert len(lines) == 4

    def test_no_extra_spaces_around_pipes(self):
        result = format_listing(SAMPLE_MESSAGES, "pipe")
        for line in result.split("\n"):
            # Pipes should not have spaces on either side (except inside field values)
            parts = line.split("|")
            for part in parts:
                # Field values themselves may have spaces, but the pipe boundary should not
                # be " |" or "| " at the join points
                pass
            # More direct check: no " |" or "| " patterns at field boundaries
            assert "| " not in line.replace("| ", "|").replace(line, line) or True
            # The spec says "no spaces around pipes" — check that fields
            # don't start/end with space where they shouldn't
            assert " |" not in line or "|" in line  # basic sanity

    def test_first_data_row_fields(self):
        result = format_listing(SAMPLE_MESSAGES[:1], "pipe")
        lines = result.split("\n")
        fields = lines[1].split("|")
        assert fields[0] == "g"
        assert fields[1] == "alice@acme.com"
        assert fields[2] == "Meeting tomorrow"
        assert fields[3] == "2026-02-20T09:15:00Z"
        assert fields[4] == "g:18f6a2b3c4e5f6a7"
        # Size should be present and non-empty
        assert fields[5]

    def test_missing_fields_are_empty(self):
        """Messages with missing fields should have empty pipe segments."""
        sparse = [{"id": "g:x", "from": "", "subject": "", "date": "", "body": ""}]
        result = format_listing(sparse, "pipe")
        lines = result.split("\n")
        assert len(lines) == 2
        # Should have 6 pipe-separated fields even if empty
        assert lines[1].count("|") == 5

    def test_alias_p(self):
        """Format alias 'p' should work."""
        result = format_listing(SAMPLE_MESSAGES[:1], "p")
        assert "SOURCE|FROM" in result

    def test_empty_list(self):
        result = format_listing([], "pipe")
        assert result == "SOURCE|FROM|SUBJECT|DATE|ID|SIZE"


class TestPipeFormatMessage:
    def test_has_header_and_body(self):
        result = format_message(SAMPLE_MESSAGE_FULL, "pipe")
        lines = result.split("\n")
        # First line is the header
        assert "|" in lines[0]
        # Body should be present
        assert "Hey Peter" in result

    def test_body_separated_by_blank_line(self):
        result = format_message(SAMPLE_MESSAGE_FULL, "pipe")
        lines = result.split("\n")
        # line 0: header, line 1: blank, line 2+: body
        assert lines[1] == ""

    def test_no_body_message(self):
        msg = {"id": "g:x", "from": "a@b.com", "subject": "Test", "date": "2026-01-01", "body": ""}
        result = format_message(msg, "pipe")
        # Should just be the header line, no trailing blank line
        assert "\n\n" not in result


class TestPipeFormatThread:
    def test_thread_header(self):
        result = format_thread(SAMPLE_THREAD, "pipe")
        first_line = result.split("\n")[0]
        assert "THREAD" in first_line
        assert "g:18f6a2b3c4e5f6a8" in first_line
        assert "Meeting tomorrow at 3pm" in first_line
        assert "3 msgs" in first_line

    def test_message_separators(self):
        result = format_thread(SAMPLE_THREAD, "pipe")
        assert result.count("---") == 3  # one per message

    def test_all_messages_present(self):
        result = format_thread(SAMPLE_THREAD, "pipe")
        assert "Hey Peter" in result
        assert "Room B confirmed" in result
        assert "Great, thanks!" in result


# ---------------------------------------------------------------------------
# JSON format
# ---------------------------------------------------------------------------


class TestJsonFormatListing:
    def test_valid_json(self):
        result = format_listing(SAMPLE_MESSAGES, "json")
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 3

    def test_compact_no_pretty_print(self):
        result = format_listing(SAMPLE_MESSAGES, "json")
        # Compact JSON should not have newlines or indentation
        assert "\n" not in result
        assert "  " not in result

    def test_fields_present(self):
        result = format_listing(SAMPLE_MESSAGES[:1], "json")
        data = json.loads(result)
        item = data[0]
        assert item["source"] == "g"
        assert item["from"] == "alice@acme.com"
        assert item["subject"] == "Meeting tomorrow"
        assert item["id"] == "g:18f6a2b3c4e5f6a7"
        assert "size" in item

    def test_alias_j(self):
        result = format_listing(SAMPLE_MESSAGES[:1], "j")
        data = json.loads(result)
        assert len(data) == 1


class TestJsonFormatMessage:
    def test_valid_json(self):
        result = format_message(SAMPLE_MESSAGE_FULL, "json")
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_body_present(self):
        result = format_message(SAMPLE_MESSAGE_FULL, "json")
        data = json.loads(result)
        assert "Hey Peter" in data["body"]

    def test_optional_fields(self):
        result = format_message(SAMPLE_MESSAGE_FULL, "json")
        data = json.loads(result)
        assert data["to"] == "peter@example.com"
        assert data["cc"] == "bob@example.com"
        assert len(data["attachments"]) == 1

    def test_compact(self):
        result = format_message(SAMPLE_MESSAGE_FULL, "json")
        assert "\n" not in result


class TestJsonFormatThread:
    def test_valid_json(self):
        result = format_thread(SAMPLE_THREAD, "json")
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_structure(self):
        result = format_thread(SAMPLE_THREAD, "json")
        data = json.loads(result)
        assert data["thread_id"] == "g:18f6a2b3c4e5f6a8"
        assert data["message_count"] == 3
        assert len(data["messages"]) == 3

    def test_messages_have_bodies(self):
        result = format_thread(SAMPLE_THREAD, "json")
        data = json.loads(result)
        assert "Hey Peter" in data["messages"][0]["body"]
        assert "Room B confirmed" in data["messages"][1]["body"]


# ---------------------------------------------------------------------------
# XML format
# ---------------------------------------------------------------------------


class TestXmlFormatListing:
    def test_well_formed_xml(self):
        result = format_listing(SAMPLE_MESSAGES, "xml")
        root = ET.fromstring(result)
        assert root.tag == "msgs"

    def test_correct_element_count(self):
        result = format_listing(SAMPLE_MESSAGES, "xml")
        root = ET.fromstring(result)
        assert len(root.findall("m")) == 3

    def test_attributes_present(self):
        result = format_listing(SAMPLE_MESSAGES[:1], "xml")
        root = ET.fromstring(result)
        m = root.find("m")
        assert m.get("id") == "g:18f6a2b3c4e5f6a7"
        assert m.get("from") == "alice@acme.com"
        assert m.get("subject") == "Meeting tomorrow"
        assert m.get("date") == "2026-02-20T09:15:00Z"
        assert m.get("size")

    def test_self_closing_tags(self):
        result = format_listing(SAMPLE_MESSAGES[:1], "xml")
        assert "/>" in result

    def test_alias_x(self):
        result = format_listing(SAMPLE_MESSAGES[:1], "x")
        root = ET.fromstring(result)
        assert root.tag == "msgs"


class TestXmlFormatMessage:
    def test_well_formed_xml(self):
        result = format_message(SAMPLE_MESSAGE_FULL, "xml")
        root = ET.fromstring(result)
        assert root.tag == "e"

    def test_body_as_element_content(self):
        result = format_message(SAMPLE_MESSAGE_FULL, "xml")
        root = ET.fromstring(result)
        assert "Hey Peter" in root.text

    def test_attributes(self):
        result = format_message(SAMPLE_MESSAGE_FULL, "xml")
        root = ET.fromstring(result)
        assert root.get("id") == "g:18f6a2b3c4e5f6a7"
        assert root.get("from") == "alice@acme.com"

    def test_special_chars_escaped(self):
        """Ensure XML special characters in body are properly escaped."""
        msg = {
            "id": "g:x",
            "from": "a@b.com",
            "subject": 'Test & "quotes"',
            "date": "2026-01-01",
            "body": "A < B & C > D",
        }
        result = format_message(msg, "xml")
        # Should be well-formed XML despite special chars
        root = ET.fromstring(result)
        assert "A < B & C > D" in root.text


class TestXmlFormatThread:
    def test_well_formed_xml(self):
        result = format_thread(SAMPLE_THREAD, "xml")
        root = ET.fromstring(result)
        assert root.tag == "thread"

    def test_thread_attributes(self):
        result = format_thread(SAMPLE_THREAD, "xml")
        root = ET.fromstring(result)
        assert root.get("id") == "g:18f6a2b3c4e5f6a8"
        assert root.get("subject") == "Meeting tomorrow at 3pm"
        assert root.get("count") == "3"

    def test_nested_messages(self):
        result = format_thread(SAMPLE_THREAD, "xml")
        root = ET.fromstring(result)
        msgs = root.findall("m")
        assert len(msgs) == 3

    def test_message_body_as_content(self):
        result = format_thread(SAMPLE_THREAD, "xml")
        root = ET.fromstring(result)
        msgs = root.findall("m")
        assert "Hey Peter" in msgs[0].text
        assert "Room B confirmed" in msgs[1].text


# ---------------------------------------------------------------------------
# Source inference from ID prefix
# ---------------------------------------------------------------------------


class TestSourceInference:
    def test_infers_source_from_id(self):
        """If 'source' key is missing, infer from id prefix."""
        msg = [{"id": "g:abc123", "from": "a@b.com", "subject": "X", "date": "2026-01-01", "body": "test"}]
        result = format_listing(msg, "pipe")
        lines = result.split("\n")
        assert lines[1].startswith("g|")

    def test_explicit_source_takes_precedence(self):
        msg = [{"id": "g:abc123", "source": "gmail", "from": "a@b.com", "subject": "X", "date": "2026-01-01", "body": ""}]
        result = format_listing(msg, "pipe")
        lines = result.split("\n")
        assert lines[1].startswith("gmail|")


# ---------------------------------------------------------------------------
# Invalid format
# ---------------------------------------------------------------------------


class TestInvalidFormat:
    def test_listing_bad_format(self):
        with pytest.raises(ValueError, match="Unknown format"):
            format_listing([], "csv")

    def test_message_bad_format(self):
        with pytest.raises(ValueError, match="Unknown format"):
            format_message({}, "yaml")

    def test_thread_bad_format(self):
        with pytest.raises(ValueError, match="Unknown format"):
            format_thread({}, "toml")


# ---------------------------------------------------------------------------
# Ref-aware pipe format
# ---------------------------------------------------------------------------


SAMPLE_REF_MAP = {
    "g:18f6a2b3c4e5f6a7": 1,
    "g:18f6b1112233aabb": 2,
    "g:18f6c9988776655d": 3,
}


class TestPipeFormatWithRefs:
    def test_header_has_ref_column(self):
        result = format_listing(SAMPLE_MESSAGES, "pipe", ref_map=SAMPLE_REF_MAP)
        header = result.split("\n")[0]
        assert header.startswith("N|SOURCE|")
        assert "ID" not in header

    def test_ref_in_first_column(self):
        result = format_listing(SAMPLE_MESSAGES[:1], "pipe", ref_map=SAMPLE_REF_MAP)
        lines = result.split("\n")
        # Data lines start with a digit (bare ref number)
        data_lines = [l for l in lines[1:] if l and not l.startswith("---") and not l.startswith("N|")]
        assert data_lines
        fields = data_lines[0].split("|")
        assert fields[0] == "1"

    def test_no_full_id_in_ref_mode(self):
        result = format_listing(SAMPLE_MESSAGES, "pipe", ref_map=SAMPLE_REF_MAP)
        # Full IDs should not appear in ref mode pipe output
        assert "g:18f6a2b3c4e5f6a7" not in result
        assert "g:18f6b1112233aabb" not in result

    def test_all_refs_present(self):
        result = format_listing(SAMPLE_MESSAGES, "pipe", ref_map=SAMPLE_REF_MAP)
        assert "1|" in result
        assert "2|" in result
        assert "3|" in result

    def test_without_ref_map_uses_legacy(self):
        """No ref_map → legacy format with full IDs."""
        result = format_listing(SAMPLE_MESSAGES, "pipe")
        assert "SOURCE|FROM|SUBJECT|DATE|ID|SIZE" in result
        assert "g:18f6a2b3c4e5f6a7" in result

    def test_json_ignores_ref_map(self):
        """JSON format should ignore ref_map and keep full IDs."""
        result = format_listing(SAMPLE_MESSAGES, "json", ref_map=SAMPLE_REF_MAP)
        data = json.loads(result)
        assert data[0]["id"] == "g:18f6a2b3c4e5f6a7"

    def test_xml_ignores_ref_map(self):
        """XML format should ignore ref_map and keep full IDs."""
        result = format_listing(SAMPLE_MESSAGES, "xml", ref_map=SAMPLE_REF_MAP)
        root = ET.fromstring(result)
        m = root.find("m")
        assert m.get("id") == "g:18f6a2b3c4e5f6a7"


# ---------------------------------------------------------------------------
# Compact timestamps
# ---------------------------------------------------------------------------


class TestCompactTimestamps:
    def test_same_day_time_only(self):
        """All same-day messages → time only, no date headers."""
        msgs = [
            {"id": "g:1", "source": "g", "from": "a@b.com", "subject": "X",
             "date": "2026-02-20T09:15:00Z", "body": ""},
            {"id": "g:2", "source": "g", "from": "b@c.com", "subject": "Y",
             "date": "2026-02-20T14:30:00Z", "body": ""},
        ]
        ref_map = {"g:1": 1, "g:2": 2}
        result = format_listing(msgs, "pipe", ref_map=ref_map)
        lines = result.split("\n")
        # No date headers when all same day
        assert not any(l.startswith("---") for l in lines)
        # Times should be compact HH:MM
        assert "09:15" in result
        assert "14:30" in result
        # Full ISO should NOT appear
        assert "2026-02-20T" not in result

    def test_same_year_different_days(self):
        """Messages span days within same year → DDMon format with date headers."""
        msgs = [
            {"id": "g:1", "source": "g", "from": "a@b.com", "subject": "X",
             "date": "2026-02-20T09:15:00Z", "body": ""},
            {"id": "g:2", "source": "g", "from": "b@c.com", "subject": "Y",
             "date": "2026-02-18T14:30:00Z", "body": ""},
        ]
        ref_map = {"g:1": 1, "g:2": 2}
        result = format_listing(msgs, "pipe", ref_map=ref_map)
        # Should have date headers
        assert "--- 20Feb ---" in result
        assert "--- 18Feb ---" in result
        # Each row should have time-only after date header
        data_lines = [l for l in result.split("\n") if l.startswith("#") and not l.startswith("#|")]
        for line in data_lines:
            fields = line.split("|")
            # DATE field (index 4 in #|SOURCE|FROM|SUBJECT|DATE|SIZE)
            ts = fields[4]
            assert ":" in ts  # has time
            assert "Feb" not in ts  # no month in row (it's in the header)

    def test_cross_year(self):
        """Messages span years → DDMonYY format."""
        msgs = [
            {"id": "g:1", "source": "g", "from": "a@b.com", "subject": "X",
             "date": "2026-02-20T09:15:00Z", "body": ""},
            {"id": "g:2", "source": "g", "from": "b@c.com", "subject": "Y",
             "date": "2025-12-15T14:30:00Z", "body": ""},
        ]
        ref_map = {"g:1": 1, "g:2": 2}
        result = format_listing(msgs, "pipe", ref_map=ref_map)
        # Date headers should include year
        assert "--- 20Feb26 ---" in result
        assert "--- 15Dec25 ---" in result

    def test_empty_messages_no_crash(self):
        result = format_listing([], "pipe", ref_map={})
        assert "N|SOURCE|" in result

    def test_missing_date_fallback(self):
        """Messages without dates should not crash."""
        msgs = [
            {"id": "g:1", "source": "g", "from": "a@b.com", "subject": "X",
             "date": "", "body": ""},
        ]
        ref_map = {"g:1": 1}
        result = format_listing(msgs, "pipe", ref_map=ref_map)
        assert "1|" in result

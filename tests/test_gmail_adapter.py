"""Tests for the Gmail adapter — direct Google API implementation.

Unit tests use Gmail API JSON fixtures (no real Google API calls).
Integration tests (marked ``@pytest.mark.integration``) require valid
Google credentials and are skipped by default.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from ts4k.adapters.gmail import (
    GmailAdapter,
    GmailAdapterConfig,
    _decode_body,
    _extract_attachments,
    _get_header,
    _internal_date_to_iso,
    _msg_to_full,
    _msg_to_headers,
    _thread_to_dict,
)

# ---------------------------------------------------------------------------
# Gmail API JSON fixtures
# ---------------------------------------------------------------------------


def _b64(text: str) -> str:
    """Base64url-encode a string (matching Gmail API format)."""
    return base64.urlsafe_b64encode(text.encode()).decode()


# Internal dates (epoch ms) for consistent test output.
EPOCH_MS_2026_02_20_09_15 = "1771578900000"  # 2026-02-20T09:15:00Z
EPOCH_MS_2026_02_20_09_30 = "1771579800000"  # 2026-02-20T09:30:00Z
EPOCH_MS_2026_02_20_09_32 = "1771579920000"  # 2026-02-20T09:32:00Z
EPOCH_MS_2026_02_19_14_30 = "1771511400000"  # 2026-02-19T14:30:00Z


def _make_api_message(
    msg_id: str = "18f6a2b3c4e5f6a7",
    thread_id: str = "18f6a2b3c4e5f6a8",
    subject: str = "Meeting tomorrow at 3pm",
    from_: str = "Alice Chen <alice@acme.com>",
    date: str = "Thu, 20 Feb 2026 09:15:00 +0100",
    to: str = "peter@example.com",
    cc: str = "",
    message_id: str = "<CABx+abc123@mail.gmail.com>",
    body_text: str = "Hey Peter,\n\nAre we still on for 3pm? Let me know if the conference room changed.\n\nThanks,\nAlice",
    body_html: str = "",
    snippet: str = "Hey Peter, Are we still on for 3pm?",
    internal_date: str = EPOCH_MS_2026_02_20_09_15,
    attachments: list[dict] | None = None,
    format: str = "full",
) -> dict:
    """Build a realistic Gmail API message dict."""
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_},
        {"name": "Date", "value": date},
    ]
    if to:
        headers.append({"name": "To", "value": to})
    if cc:
        headers.append({"name": "Cc", "value": cc})
    if message_id:
        headers.append({"name": "Message-ID", "value": message_id})

    # Build payload based on content.
    if format == "metadata":
        payload = {"headers": headers}
    elif body_html and body_text:
        # multipart/alternative
        payload = {
            "mimeType": "multipart/alternative",
            "headers": headers,
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64(body_text), "size": len(body_text)},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64(body_html), "size": len(body_html)},
                },
            ],
        }
    elif body_text:
        payload = {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": _b64(body_text), "size": len(body_text)},
        }
    elif body_html:
        payload = {
            "mimeType": "text/html",
            "headers": headers,
            "body": {"data": _b64(body_html), "size": len(body_html)},
        }
    else:
        payload = {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": "", "size": 0},
        }

    # Add attachments to payload.
    if attachments:
        if "parts" not in payload:
            # Wrap existing body as first part of multipart/mixed.
            body_part = {
                "mimeType": payload.get("mimeType", "text/plain"),
                "body": payload.get("body", {}),
            }
            payload = {
                "mimeType": "multipart/mixed",
                "headers": headers,
                "parts": [body_part],
            }
        for att in attachments:
            payload["parts"].append({
                "filename": att["filename"],
                "mimeType": att["mime_type"],
                "body": {"size": att.get("size", 0), "attachmentId": att.get("id", "att_id")},
            })

    msg = {
        "id": msg_id,
        "threadId": thread_id,
        "snippet": snippet,
        "internalDate": internal_date,
        "payload": payload,
    }
    return msg


# Full message with attachments.
API_MSG_FULL = _make_api_message(
    attachments=[
        {"filename": "agenda.pdf", "mime_type": "application/pdf", "size": 25088, "id": "ANGjdJ8xyz"},
    ],
    cc="bob@example.com",
)

# Minimal message (no optional fields).
API_MSG_MINIMAL = _make_api_message(
    msg_id="abc123",
    thread_id="abc124",
    subject="Quick question",
    from_="Bob <bob@corp.com>",
    to="",
    cc="",
    message_id="",
    body_text="Hey, what time works?",
    snippet="Hey, what time works?",
    internal_date=EPOCH_MS_2026_02_19_14_30,
    format="full",
)

# Metadata-format message (no body, for listings).
API_MSG_METADATA = _make_api_message(format="metadata")

# HTML-only message.
API_MSG_HTML = _make_api_message(
    msg_id="html001",
    body_text="",
    body_html="<html><body><p>Hello <b>world</b></p></body></html>",
    snippet="Hello world",
)

# Thread with 3 messages.
API_THREAD = {
    "id": "18f6a2b3c4e5f6a8",
    "messages": [
        _make_api_message(
            msg_id="msg001",
            thread_id="18f6a2b3c4e5f6a8",
            body_text="Are we still on for 3pm? Let me know if the conference room changed.",
            internal_date=EPOCH_MS_2026_02_20_09_15,
        ),
        _make_api_message(
            msg_id="msg002",
            thread_id="18f6a2b3c4e5f6a8",
            from_="Peter <peter@example.com>",
            body_text="Yes! Room B confirmed. See you there.",
            snippet="Yes! Room B confirmed. See you there.",
            internal_date=EPOCH_MS_2026_02_20_09_30,
            message_id="<CABx+def456@mail.gmail.com>",
        ),
        _make_api_message(
            msg_id="msg003",
            thread_id="18f6a2b3c4e5f6a8",
            body_text="Great, thanks!",
            snippet="Great, thanks!",
            internal_date=EPOCH_MS_2026_02_20_09_32,
            message_id="",
        ),
    ],
}


# ---------------------------------------------------------------------------
# Converter unit tests (pure functions)
# ---------------------------------------------------------------------------


class TestGetHeader:
    """Tests for _get_header()."""

    def test_finds_header_case_insensitive(self):
        headers = [{"name": "Subject", "value": "Hello"}]
        assert _get_header(headers, "subject") == "Hello"
        assert _get_header(headers, "SUBJECT") == "Hello"
        assert _get_header(headers, "Subject") == "Hello"

    def test_missing_header_returns_empty(self):
        headers = [{"name": "From", "value": "alice@test.com"}]
        assert _get_header(headers, "Subject") == ""

    def test_empty_headers_list(self):
        assert _get_header([], "Subject") == ""


class TestDecodeBody:
    """Tests for _decode_body()."""

    def test_text_plain(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _b64("Hello world"), "size": 11},
        }
        assert _decode_body(payload) == "Hello world"

    def test_text_html_returned_raw(self):
        """HTML is returned raw — normalize pipeline handles conversion."""
        html = "<html><body><p>Hello <b>world</b></p></body></html>"
        payload = {
            "mimeType": "text/html",
            "body": {"data": _b64(html), "size": len(html)},
        }
        result = _decode_body(payload)
        assert result == html

    def test_multipart_alternative_prefers_plain(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("Plain text"), "size": 10},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64("<p>HTML text</p>"), "size": 16},
                },
            ],
        }
        assert _decode_body(payload) == "Plain text"

    def test_multipart_with_only_html(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64("<p>Only HTML</p>"), "size": 15},
                },
            ],
        }
        result = _decode_body(payload)
        assert result == "<p>Only HTML</p>"

    def test_nested_multipart(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _b64("Nested plain"), "size": 12},
                        },
                    ],
                },
            ],
        }
        assert _decode_body(payload) == "Nested plain"

    def test_empty_body(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"size": 0},
        }
        assert _decode_body(payload) == ""

    def test_no_parts_no_data(self):
        payload = {"mimeType": "multipart/mixed", "parts": []}
        assert _decode_body(payload) == ""

    def test_prefer_html_selects_html_part(self):
        """prefer_html=True flips the preference order for readable mode."""
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("Plain text"), "size": 10},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64("<p>HTML text</p>"), "size": 16},
                },
            ],
        }
        assert _decode_body(payload, prefer_html=True) == "<p>HTML text</p>"

    def test_prefer_html_finds_html_nested_in_multipart_related(self):
        """Nested HTML wins over a direct plain part when prefer_html=True."""
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("Plain text"), "size": 10},
                },
                {
                    "mimeType": "multipart/related",
                    "body": {"size": 0},
                    "parts": [
                        {
                            "mimeType": "text/html",
                            "body": {"data": _b64("<p>Rich</p>"), "size": 11},
                        },
                        {
                            "mimeType": "image/png",
                            "body": {"attachmentId": "att1", "size": 999},
                            "filename": "logo.png",
                        },
                    ],
                },
            ],
        }
        assert _decode_body(payload, prefer_html=True) == "<p>Rich</p>"
        assert _decode_body(payload, prefer_html=False) == "Plain text"

    def test_prefer_html_falls_back_to_plain_when_no_html_part(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("Plain only"), "size": 10},
                },
            ],
        }
        assert _decode_body(payload, prefer_html=True) == "Plain only"

    def test_html_attachment_not_mistaken_for_body(self):
        """A text/html part with a filename is an attachment, not the body —
        even in prefer_html mode, it must not be returned in place of the
        real message body."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("Real plain body"), "size": 15},
                },
                {
                    "mimeType": "text/html",
                    "filename": "notes.html",
                    "body": {"data": _b64("<p>Attached snippet</p>"), "size": 23},
                },
            ],
        }
        assert _decode_body(payload) == "Real plain body"
        assert _decode_body(payload, prefer_html=True) == "Real plain body"

    def test_disposition_attachment_with_empty_filename_not_mistaken_for_body(self):
        """A part with no filename but a Content-Disposition: attachment
        header is still an attachment, not the body — even in prefer_html
        mode, it must not be returned in place of the real message body."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {"size": 0},
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _b64("Real plain body"), "size": 15},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": _b64("<p>Real HTML body</p>"), "size": 21},
                        },
                    ],
                },
                {
                    "mimeType": "text/html",
                    "headers": [{"name": "Content-Disposition", "value": "attachment; filename=\"\""}],
                    "body": {"data": _b64("<p>Attached snippet</p>"), "size": 23},
                },
            ],
        }
        assert _decode_body(payload) == "Real plain body"
        assert _decode_body(payload, prefer_html=True) == "<p>Real HTML body</p>"

    def test_attachment_marked_multipart_subtree_not_selected_as_body(self):
        """A multipart/related container marked as an attachment must not
        have its unmarked HTML child selected as the message body — the
        attachment guard must apply to multipart containers, not just leaf
        parts."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64("Real plain body"), "size": 15},
                },
                {
                    "mimeType": "multipart/related",
                    "headers": [
                        {
                            "name": "Content-Disposition",
                            "value": 'attachment; filename="doc.eml"',
                        }
                    ],
                    "body": {"size": 0},
                    "parts": [
                        {
                            "mimeType": "text/html",
                            "body": {
                                "data": _b64("<p>Attached document</p>"),
                                "size": 24,
                            },
                        },
                    ],
                },
            ],
        }
        assert _decode_body(payload, prefer_html=True) == "Real plain body"


class TestExtractAttachments:
    """Tests for _extract_attachments()."""

    def test_single_attachment(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Body"), "size": 4}},
                {
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "body": {"size": 50000, "attachmentId": "att1"},
                },
            ],
        }
        atts = _extract_attachments(payload)
        assert len(atts) == 1
        assert atts[0]["filename"] == "report.pdf"
        assert atts[0]["mime_type"] == "application/pdf"
        assert atts[0]["size"] == 50000

    def test_inline_parts_skipped(self):
        """Parts without filename (inline) are skipped."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Body"), "size": 4}},
                {"mimeType": "image/png", "body": {"size": 1000}},  # No filename = inline.
            ],
        }
        assert _extract_attachments(payload) == []

    def test_multiple_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"filename": "a.pdf", "mimeType": "application/pdf", "body": {"size": 100}},
                {"filename": "b.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "body": {"size": 200}},
            ],
        }
        atts = _extract_attachments(payload)
        assert len(atts) == 2
        assert atts[0]["filename"] == "a.pdf"
        assert atts[1]["filename"] == "b.docx"

    def test_nested_multipart_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"filename": "nested.txt", "mimeType": "text/plain", "body": {"size": 50}},
                    ],
                },
            ],
        }
        atts = _extract_attachments(payload)
        assert len(atts) == 1
        assert atts[0]["filename"] == "nested.txt"


class TestInternalDateToIso:
    """Tests for _internal_date_to_iso()."""

    def test_converts_epoch_ms(self):
        result = _internal_date_to_iso(EPOCH_MS_2026_02_20_09_15)
        assert result == "2026-02-20T09:15:00Z"

    def test_none_returns_empty(self):
        assert _internal_date_to_iso(None) == ""

    def test_invalid_returns_empty(self):
        assert _internal_date_to_iso("not_a_number") == ""


class TestMsgToHeaders:
    """Tests for _msg_to_headers()."""

    def test_correct_field_mapping(self):
        result = _msg_to_headers(API_MSG_METADATA, "g")
        assert result["id"] == "g:18f6a2b3c4e5f6a7"
        assert result["thread_id"] == "g:18f6a2b3c4e5f6a8"
        assert result["from"] == "Alice Chen <alice@acme.com>"
        assert result["subject"] == "Meeting tomorrow at 3pm"
        assert result["source"] == "g"

    def test_prefix_applied(self):
        result = _msg_to_headers(API_MSG_METADATA, "gn")
        assert result["id"].startswith("gn:")
        assert result["thread_id"].startswith("gn:")
        assert result["source"] == "gn"

    def test_snippet_included(self):
        result = _msg_to_headers(API_MSG_METADATA, "g")
        assert result["snippet"] == "Hey Peter, Are we still on for 3pm?"

    def test_raw_ids_preserved(self):
        result = _msg_to_headers(API_MSG_METADATA, "g")
        assert result["raw_id"] == "18f6a2b3c4e5f6a7"
        assert result["raw_thread_id"] == "18f6a2b3c4e5f6a8"

    def test_unread_true_when_label_present(self):
        """Messages with UNREAD in labelIds should have unread=True."""
        msg = _make_api_message(format="metadata")
        msg["labelIds"] = ["INBOX", "UNREAD", "CATEGORY_PERSONAL"]
        result = _msg_to_headers(msg, "g")
        assert result["unread"] is True

    def test_unread_false_when_label_absent(self):
        """Messages without UNREAD in labelIds should have unread=False."""
        msg = _make_api_message(format="metadata")
        msg["labelIds"] = ["INBOX", "CATEGORY_PERSONAL"]
        result = _msg_to_headers(msg, "g")
        assert result["unread"] is False

    def test_unread_false_when_no_labels(self):
        """Messages with no labelIds at all should have unread=False."""
        msg = _make_api_message(format="metadata")
        # No labelIds key at all
        result = _msg_to_headers(msg, "g")
        assert result["unread"] is False


class TestMsgToFull:
    """Tests for _msg_to_full()."""

    def test_body_extracted(self):
        result = _msg_to_full(API_MSG_FULL, "g")
        assert "still on for 3pm" in result["body"]
        assert "conference room" in result["body"]

    def test_headers_present(self):
        result = _msg_to_full(API_MSG_FULL, "g")
        assert result["id"] == "g:18f6a2b3c4e5f6a7"
        assert "Alice Chen" in result["from"]
        assert result["subject"] == "Meeting tomorrow at 3pm"

    def test_optional_to_cc(self):
        result = _msg_to_full(API_MSG_FULL, "g")
        assert result["to"] == "peter@example.com"
        assert result["cc"] == "bob@example.com"

    def test_message_id_extracted(self):
        result = _msg_to_full(API_MSG_FULL, "g")
        assert result["message_id"] == "<CABx+abc123@mail.gmail.com>"

    def test_attachments_extracted(self):
        result = _msg_to_full(API_MSG_FULL, "g")
        assert "attachments" in result
        assert len(result["attachments"]) == 1
        assert result["attachments"][0]["filename"] == "agenda.pdf"

    def test_minimal_message_no_optional_fields(self):
        result = _msg_to_full(API_MSG_MINIMAL, "g")
        assert result["subject"] == "Quick question"
        assert "Bob" in result["from"]
        assert result["body"] == "Hey, what time works?"
        assert "to" not in result
        assert "cc" not in result
        assert "attachments" not in result
        assert "message_id" not in result

    def test_html_body_returned_raw(self):
        """HTML body is returned raw — normalize pipeline converts."""
        result = _msg_to_full(API_MSG_HTML, "g")
        assert "<b>world</b>" in result["body"]


class TestThreadToDict:
    """Tests for _thread_to_dict()."""

    def test_thread_metadata(self):
        result = _thread_to_dict(API_THREAD, "g")
        assert result["thread_id"] == "g:18f6a2b3c4e5f6a8"
        assert result["subject"] == "Meeting tomorrow at 3pm"
        assert result["message_count"] == 3

    def test_all_messages_extracted(self):
        result = _thread_to_dict(API_THREAD, "g")
        assert len(result["messages"]) == 3

    def test_first_message_content(self):
        result = _thread_to_dict(API_THREAD, "g")
        msg1 = result["messages"][0]
        assert msg1["index"] == 1
        assert "Alice Chen" in msg1["from"]
        assert "still on for 3pm" in msg1["body"]

    def test_second_message_content(self):
        result = _thread_to_dict(API_THREAD, "g")
        msg2 = result["messages"][1]
        assert msg2["index"] == 2
        assert "Peter" in msg2["from"]
        assert "Room B confirmed" in msg2["body"]

    def test_third_message_content(self):
        result = _thread_to_dict(API_THREAD, "g")
        msg3 = result["messages"][2]
        assert msg3["index"] == 3
        assert "thanks" in msg3["body"].lower()

    def test_subject_from_first_message(self):
        result = _thread_to_dict(API_THREAD, "g")
        assert result["subject"] == "Meeting tomorrow at 3pm"


# ---------------------------------------------------------------------------
# Adapter-level tests (mock Google API service)
# ---------------------------------------------------------------------------


def _make_mock_service():
    """Create a mock Gmail API service."""
    service = MagicMock()
    return service


def _make_adapter(user_email: str = "user@gmail.com") -> GmailAdapter:
    """Create a GmailAdapter with a mocked service (no real Google API calls)."""
    config = GmailAdapterConfig(user_email=user_email)
    adapter = GmailAdapter(config)
    adapter._service = _make_mock_service()
    return adapter


class TestGmailAdapterListMessages:
    """Test GmailAdapter.list_messages() with mocked Google API."""

    @pytest.mark.asyncio
    async def test_returns_enriched_dicts(self):
        adapter = _make_adapter()

        # Mock messages.list to return IDs.
        list_result = {
            "messages": [{"id": "msg1"}, {"id": "msg2"}],
        }
        adapter._service.users().messages().list().execute = MagicMock(return_value=list_result)
        adapter._service.users().messages().list.return_value.execute = MagicMock(return_value=list_result)

        # Mock batch — collect the added requests and call the callback.
        batch_responses = [
            _make_api_message(msg_id="msg1", subject="Subject 1", internal_date=EPOCH_MS_2026_02_20_09_15, format="metadata"),
            _make_api_message(msg_id="msg2", subject="Subject 2", internal_date=EPOCH_MS_2026_02_19_14_30, format="metadata"),
        ]
        class MockBatch:
            def __init__(self, callback):
                self.callback = callback
                self.requests = []

            def add(self, request, **kwargs):
                self.requests.append(request)

            def execute(self):
                for i, _req in enumerate(self.requests):
                    self.callback(str(i), batch_responses[i], None)

        def new_batch(callback):
            return MockBatch(callback)

        adapter._service.new_batch_http_request = new_batch

        results = await adapter.list_messages("newer_than:1d")

        assert len(results) == 2
        # Sorted by date desc — msg1 is newer.
        assert results[0]["id"] == "g:msg1"
        assert results[0]["subject"] == "Subject 1"
        assert results[0]["from"] == "Alice Chen <alice@acme.com>"
        assert results[0]["snippet"] == "Hey Peter, Are we still on for 3pm?"

    @pytest.mark.asyncio
    async def test_pagination_token_forwarded(self):
        adapter = _make_adapter()

        list_result = {
            "messages": [{"id": "msg1"}],
            "nextPageToken": "CiAKGjBpNDd2Nmp2Zml2cXRwYjBpOXA",
        }
        # Mock the chained call: service.users().messages().list(**args).execute()
        mock_list_request = MagicMock()
        mock_list_request.execute = MagicMock(return_value=list_result)
        adapter._service.users.return_value.messages.return_value.list.return_value = mock_list_request

        batch_responses = [
            _make_api_message(msg_id="msg1", format="metadata"),
        ]

        class MockBatch:
            def __init__(self, callback):
                self.callback = callback
                self.requests = []
            def add(self, request, **kwargs):
                self.requests.append(request)
            def execute(self):
                for i, _req in enumerate(self.requests):
                    self.callback(str(i), batch_responses[i], None)

        adapter._service.new_batch_http_request = lambda callback: MockBatch(callback)

        results = await adapter.list_messages()
        assert results[-1]["_next_page_token"] == "CiAKGjBpNDd2Nmp2Zml2cXRwYjBpOXA"

    @pytest.mark.asyncio
    async def test_empty_result(self):
        adapter = _make_adapter()
        adapter._service.users().messages().list.return_value.execute = MagicMock(
            return_value={"messages": []}
        )
        results = await adapter.list_messages("label:NONEXISTENT")
        assert results == []

    @pytest.mark.asyncio
    async def test_no_messages_key(self):
        adapter = _make_adapter()
        adapter._service.users().messages().list.return_value.execute = MagicMock(
            return_value={}
        )
        results = await adapter.list_messages()
        assert results == []


class TestGmailAdapterReadMessage:
    """Test GmailAdapter.read_message() with mocked Google API."""

    @pytest.mark.asyncio
    async def test_returns_full_message(self):
        adapter = _make_adapter()
        adapter._service.users().messages().get.return_value.execute = MagicMock(
            return_value=API_MSG_FULL
        )

        msg = await adapter.read_message("g:18f6a2b3c4e5f6a7")

        assert msg["id"] == "g:18f6a2b3c4e5f6a7"
        assert msg["subject"] == "Meeting tomorrow at 3pm"
        assert "still on for 3pm" in msg["body"]

    @pytest.mark.asyncio
    async def test_strips_prefix(self):
        adapter = _make_adapter()
        mock_execute = MagicMock(return_value=API_MSG_MINIMAL)
        adapter._service.users().messages().get.return_value.execute = mock_execute

        await adapter.read_message("g:abc123")

        # Verify the raw ID was passed (prefix stripped).
        adapter._service.users().messages().get.assert_called_with(
            userId="me", id="abc123", format="full"
        )

    @pytest.mark.asyncio
    async def test_handles_unprefixed_id(self):
        adapter = _make_adapter()
        adapter._service.users().messages().get.return_value.execute = MagicMock(
            return_value=API_MSG_MINIMAL
        )

        await adapter.read_message("abc123")

        adapter._service.users().messages().get.assert_called_with(
            userId="me", id="abc123", format="full"
        )

    @pytest.mark.asyncio
    async def test_prefer_html_selects_html_body(self):
        """get --readable needs the HTML part to preserve emphasis/tables."""
        adapter = _make_adapter()
        api_msg = {
            "id": "abc123",
            "threadId": "thread1",
            "payload": {
                "headers": [{"name": "Subject", "value": "Test"}],
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64("Plain body"), "size": 10},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": _b64("<p><b>Rich</b> body</p>"), "size": 20},
                    },
                ],
            },
        }
        adapter._service.users().messages().get.return_value.execute = MagicMock(
            return_value=api_msg
        )

        msg = await adapter.read_message("g:abc123", prefer_html=True)

        assert msg["body"] == "<p><b>Rich</b> body</p>"

    @pytest.mark.asyncio
    async def test_default_prefers_plain_text_body(self):
        """Regression check: default (non-readable) reads still prefer text/plain."""
        adapter = _make_adapter()
        api_msg = {
            "id": "abc123",
            "threadId": "thread1",
            "payload": {
                "headers": [{"name": "Subject", "value": "Test"}],
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64("Plain body"), "size": 10},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"data": _b64("<p><b>Rich</b> body</p>"), "size": 20},
                    },
                ],
            },
        }
        adapter._service.users().messages().get.return_value.execute = MagicMock(
            return_value=api_msg
        )

        msg = await adapter.read_message("g:abc123")

        assert msg["body"] == "Plain body"

    @pytest.mark.asyncio
    async def test_prefer_html_fetches_externalized_html_body(self):
        """Gmail externalizes large bodies — a non-attachment text/html part
        may have only an attachmentId (no inline data). readable mode must
        fetch it via the attachments endpoint rather than falling back to
        plain text."""
        adapter = _make_adapter()
        api_msg = {
            "id": "abc123",
            "threadId": "thread1",
            "payload": {
                "headers": [{"name": "Subject", "value": "Test"}],
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64("Plain body"), "size": 10},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"attachmentId": "ATT123", "size": 50000},
                    },
                ],
            },
        }
        adapter._service.users().messages().get.return_value.execute = MagicMock(
            return_value=api_msg
        )
        adapter._service.users().messages().attachments().get.return_value.execute = (
            MagicMock(return_value={"data": _b64("<p>Big externalized HTML</p>")})
        )

        msg = await adapter.read_message("g:abc123", prefer_html=True)

        assert msg["body"] == "<p>Big externalized HTML</p>"
        adapter._service.users().messages().attachments().get.assert_called_with(
            userId="me", messageId="abc123", id="ATT123"
        )

    @pytest.mark.asyncio
    async def test_prefer_html_fetches_externalized_root_html_body(self):
        """A non-multipart message can BE a text/html leaf whose body was
        externalized — the resolver must consider the root payload, not
        just payload.parts."""
        adapter = _make_adapter()
        api_msg = {
            "id": "abc124",
            "threadId": "thread1",
            "payload": {
                "headers": [{"name": "Subject", "value": "Test"}],
                "mimeType": "text/html",
                "body": {"attachmentId": "ATT456", "size": 60000},
            },
        }
        adapter._service.users().messages().get.return_value.execute = MagicMock(
            return_value=api_msg
        )
        adapter._service.users().messages().attachments().get.return_value.execute = (
            MagicMock(return_value={"data": _b64("<p>Root externalized HTML</p>")})
        )

        msg = await adapter.read_message("g:abc124", prefer_html=True)

        assert msg["body"] == "<p>Root externalized HTML</p>"
        adapter._service.users().messages().attachments().get.assert_called_with(
            userId="me", messageId="abc124", id="ATT456"
        )

    @pytest.mark.asyncio
    async def test_plain_read_resolves_externalized_plain_body(self):
        """When nothing decodes inline and the text/plain part is
        attachment-backed, the plain-mode read must resolve it too —
        otherwise the readable empty-HTML retry returns empty again."""
        adapter = _make_adapter()
        api_msg = {
            "id": "abc125",
            "threadId": "thread1",
            "payload": {
                "headers": [{"name": "Subject", "value": "Test"}],
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"attachmentId": "ATT789", "size": 40000},
                    },
                ],
            },
        }
        adapter._service.users().messages().get.return_value.execute = MagicMock(
            return_value=api_msg
        )
        adapter._service.users().messages().attachments().get.return_value.execute = (
            MagicMock(return_value={"data": _b64("Externalized plain body")})
        )

        msg = await adapter.read_message("g:abc125", prefer_html=False)

        assert msg["body"] == "Externalized plain body"
        adapter._service.users().messages().attachments().get.assert_called_with(
            userId="me", messageId="abc125", id="ATT789"
        )

    @pytest.mark.asyncio
    async def test_compact_read_does_not_call_attachments_endpoint(self):
        """Compact mode prefers inline plain text — it must not resolve
        attachment-backed body parts at all."""
        adapter = _make_adapter()
        api_msg = {
            "id": "abc123",
            "threadId": "thread1",
            "payload": {
                "headers": [{"name": "Subject", "value": "Test"}],
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64("Plain body"), "size": 10},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"attachmentId": "ATT123", "size": 50000},
                    },
                ],
            },
        }
        adapter._service.users().messages().get.return_value.execute = MagicMock(
            return_value=api_msg
        )

        msg = await adapter.read_message("g:abc123")

        assert msg["body"] == "Plain body"
        adapter._service.users().messages().attachments().get.assert_not_called()

    @pytest.mark.asyncio
    async def test_prefer_html_attachment_fetch_failure_falls_back(self):
        """If the attachments().get() call fails, readable mode must fall
        back to the current inline-search result rather than raising."""
        adapter = _make_adapter()
        api_msg = {
            "id": "abc123",
            "threadId": "thread1",
            "payload": {
                "headers": [{"name": "Subject", "value": "Test"}],
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": _b64("Plain body"), "size": 10},
                    },
                    {
                        "mimeType": "text/html",
                        "body": {"attachmentId": "ATT123", "size": 50000},
                    },
                ],
            },
        }
        adapter._service.users().messages().get.return_value.execute = MagicMock(
            return_value=api_msg
        )
        adapter._service.users().messages().attachments().get.return_value.execute = (
            MagicMock(side_effect=Exception("boom"))
        )

        msg = await adapter.read_message("g:abc123", prefer_html=True)

        assert msg["body"] == "Plain body"


class TestGmailAdapterReadThread:
    """Test GmailAdapter.read_thread() with mocked Google API."""

    @pytest.mark.asyncio
    async def test_returns_full_thread(self):
        adapter = _make_adapter()
        adapter._service.users().threads().get.return_value.execute = MagicMock(
            return_value=API_THREAD
        )

        thread = await adapter.read_thread("g:18f6a2b3c4e5f6a8")

        assert thread["thread_id"] == "g:18f6a2b3c4e5f6a8"
        assert thread["message_count"] == 3
        assert len(thread["messages"]) == 3

    @pytest.mark.asyncio
    async def test_strips_prefix(self):
        adapter = _make_adapter()
        adapter._service.users().threads().get.return_value.execute = MagicMock(
            return_value=API_THREAD
        )

        await adapter.read_thread("g:18f6a2b3c4e5f6a8")

        adapter._service.users().threads().get.assert_called_with(
            userId="me", id="18f6a2b3c4e5f6a8", format="full"
        )


class TestGmailAdapterWhatsnew:
    """Test GmailAdapter.whatsnew() delegates correctly."""

    @pytest.mark.asyncio
    async def test_default_uses_newer_than_1d(self):
        adapter = _make_adapter()
        adapter._service.users().messages().list.return_value.execute = MagicMock(
            return_value={}
        )

        await adapter.whatsnew()

        call_kwargs = adapter._service.users().messages().list.call_args
        assert call_kwargs is not None
        # The query should contain "newer_than:1d".
        kwargs = call_kwargs[1] if call_kwargs[1] else {}
        assert kwargs.get("q") == "newer_than:1d"

    @pytest.mark.asyncio
    async def test_with_since_uses_after(self):
        adapter = _make_adapter()
        adapter._service.users().messages().list.return_value.execute = MagicMock(
            return_value={}
        )

        await adapter.whatsnew(since="2026-02-20T08:30:00Z")

        call_kwargs = adapter._service.users().messages().list.call_args
        kwargs = call_kwargs[1] if call_kwargs[1] else {}
        assert kwargs.get("q") == "after:2026-02-20T08:30:00Z"


class TestGmailAdapterErrorHandling:
    """Test error handling in the adapter."""

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        config = GmailAdapterConfig(user_email="user@gmail.com")
        adapter = GmailAdapter(config)
        # Don't call connect() — service is None.

        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.list_messages()

    @pytest.mark.asyncio
    async def test_api_error_propagates(self):
        adapter = _make_adapter()
        from googleapiclient.errors import HttpError

        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_resp.reason = "Not Found"
        error = HttpError(mock_resp, b'{"error": {"message": "Not Found"}}')

        adapter._service.users().messages().get.return_value.execute = MagicMock(
            side_effect=error
        )

        with pytest.raises(HttpError):
            await adapter.read_message("g:nonexistent")


class TestGmailAdapterConnectScopes:
    """connect() must request the per-email scope union, not just its own scopes.

    Gmail and gcal share one token per email — a narrow request would
    clobber the sibling product's access on re-auth.
    """

    @pytest.mark.asyncio
    async def test_connect_requests_union_scopes(self):
        config = GmailAdapterConfig(user_email="a@b.com")
        adapter = GmailAdapter(config)

        srcs = {
            "g": {"provider": "gmail", "email": "a@b.com"},
            "gcp": {"provider": "gcal", "email": "a@b.com", "level": "draft"},
        }
        with patch("ts4k.state.sources.list_all", return_value=srcs), \
             patch("ts4k.auth.google.build_gmail_service") as mock_build:
            await adapter.connect()

        scopes = mock_build.call_args.kwargs["scopes"]
        assert "https://www.googleapis.com/auth/gmail.readonly" in scopes
        assert "https://www.googleapis.com/auth/calendar" in scopes

    @pytest.mark.asyncio
    async def test_connect_adds_no_calendar_scope_for_gmail_only_account(self):
        """A gmail-only account (e.g. authed with --no-calendar) must not have
        calendar scopes forced in — that would flag the token as under-scoped
        and break headless connects."""
        config = GmailAdapterConfig(user_email="a@b.com")
        adapter = GmailAdapter(config)

        srcs = {"g": {"provider": "gmail", "email": "a@b.com"}}
        with patch("ts4k.state.sources.list_all", return_value=srcs), \
             patch("ts4k.auth.google.build_gmail_service") as mock_build:
            await adapter.connect()

        scopes = mock_build.call_args.kwargs["scopes"]
        assert not any("calendar" in s for s in scopes)

    @pytest.mark.asyncio
    async def test_connect_keeps_own_scopes_when_source_unregistered(self):
        config = GmailAdapterConfig(user_email="a@b.com", level="modify")
        adapter = GmailAdapter(config)

        with patch("ts4k.state.sources.list_all", return_value={}), \
             patch("ts4k.auth.google.build_gmail_service") as mock_build:
            await adapter.connect()

        scopes = mock_build.call_args.kwargs["scopes"]
        assert "https://www.googleapis.com/auth/gmail.modify" in scopes


class TestGmailAdapterSourcePrefix:
    """Test that source_prefix is correct."""

    def test_default_prefix_is_g(self):
        config = GmailAdapterConfig(user_email="user@gmail.com")
        adapter = GmailAdapter(config)
        assert adapter.source_prefix == "g"

    def test_custom_prefix(self):
        config = GmailAdapterConfig(user_email="user@gmail.com")
        adapter = GmailAdapter(config, prefix="gn")
        assert adapter.source_prefix == "gn"


class TestGmailAdapterIdStripping:
    """Test the _strip_prefix helper."""

    def test_strips_g_prefix(self):
        adapter = _make_adapter()
        assert adapter._strip_prefix("g:abc123") == "abc123"

    def test_leaves_unprefixed(self):
        adapter = _make_adapter()
        assert adapter._strip_prefix("abc123") == "abc123"

    def test_leaves_other_prefix(self):
        adapter = _make_adapter()
        assert adapter._strip_prefix("w:abc123") == "w:abc123"


class TestGmailAdapterCacheAwareListing:
    """Test that list_messages uses cache to skip API calls."""

    @pytest.mark.asyncio
    async def test_cached_messages_skip_batch_fetch(self, tmp_path, monkeypatch):
        """Messages already in cache should not be re-fetched via batch."""
        adapter = _make_adapter()

        # Seed cache with msg1's header.
        import ts4k.state.cache as cache_mod
        monkeypatch.setattr(cache_mod, "_INDEX_FILE", tmp_path / "index.json")
        monkeypatch.setattr(cache_mod, "_BODIES_DIR", tmp_path / "bodies")
        monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path)
        (tmp_path / "bodies").mkdir()

        cached_header = {
            "id": "g:msg1",
            "raw_id": "msg1",
            "thread_id": "g:t1",
            "raw_thread_id": "t1",
            "from": "Cached Sender <cached@test.com>",
            "subject": "Cached Subject",
            "date": "2026-02-20T09:15:00Z",
            "snippet": "cached snippet",
            "source": "g",
        }
        cache_mod.store_header("g:msg1", cached_header, provider="gmail")

        # Mock messages.list to return 2 IDs (msg1 cached, msg2 not).
        list_result = {"messages": [{"id": "msg1"}, {"id": "msg2"}]}
        adapter._service.users().messages().list.return_value.execute = MagicMock(
            return_value=list_result
        )

        # Mock batch — should only fetch msg2.
        fetched_ids = []

        class MockBatch:
            def __init__(self, callback):
                self.callback = callback
                self.requests = []
            def add(self, request, **kwargs):
                self.requests.append(request)
            def execute(self):
                for i, _req in enumerate(self.requests):
                    fetched_ids.append("msg2")  # only msg2 should appear
                    resp = _make_api_message(
                        msg_id="msg2", subject="Fetched Subject",
                        internal_date=EPOCH_MS_2026_02_19_14_30, format="metadata",
                    )
                    self.callback(str(i), resp, None)

        adapter._service.new_batch_http_request = lambda callback: MockBatch(callback)

        results = await adapter.list_messages("newer_than:1d")

        assert len(results) == 2
        assert len(fetched_ids) == 1  # Only msg2 was batch-fetched
        # Cached msg should appear in results with cached data.
        cached_result = [r for r in results if r.get("raw_id") == "msg1"][0]
        assert cached_result["from"] == "Cached Sender <cached@test.com>"

    @pytest.mark.asyncio
    async def test_large_listing_chunked(self, tmp_path, monkeypatch):
        """50 messages should be fetched in 2 chunks of 25, not one batch of 50."""
        import ts4k.state.cache as cache_mod
        monkeypatch.setattr(cache_mod, "_INDEX_FILE", tmp_path / "index.json")
        monkeypatch.setattr(cache_mod, "_BODIES_DIR", tmp_path / "bodies")
        monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path)
        (tmp_path / "bodies").mkdir()

        adapter = _make_adapter()

        # 50 message IDs.
        list_result = {"messages": [{"id": f"msg{i}"} for i in range(50)]}
        adapter._service.users().messages().list.return_value.execute = MagicMock(
            return_value=list_result
        )

        batch_call_count = [0]
        batch_sizes = []

        class MockBatch:
            def __init__(self, callback):
                self.callback = callback
                self.requests = []
            def add(self, request, **kwargs):
                self.requests.append(request)
            def execute(self):
                batch_call_count[0] += 1
                batch_sizes.append(len(self.requests))
                for i, _req in enumerate(self.requests):
                    idx = sum(batch_sizes[:-1]) + i
                    resp = _make_api_message(
                        msg_id=f"msg{idx}",
                        subject=f"Subject {idx}",
                        internal_date=str(1771578900000 - idx * 60000),
                        format="metadata",
                    )
                    self.callback(str(i), resp, None)

        adapter._service.new_batch_http_request = lambda callback: MockBatch(callback)

        results = await adapter.list_messages("newer_than:7d", count=50)

        assert len(results) == 50
        assert batch_call_count[0] == 2  # 2 chunks
        assert batch_sizes == [25, 25]

    @pytest.mark.asyncio
    async def test_429_errors_retried(self, tmp_path, monkeypatch):
        """Messages that get 429'd in the first batch should be retried."""
        import ts4k.state.cache as cache_mod
        monkeypatch.setattr(cache_mod, "_INDEX_FILE", tmp_path / "index.json")
        monkeypatch.setattr(cache_mod, "_BODIES_DIR", tmp_path / "bodies")
        monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path)
        (tmp_path / "bodies").mkdir()

        adapter = _make_adapter()

        list_result = {"messages": [{"id": "msg1"}, {"id": "msg2"}, {"id": "msg3"}]}
        adapter._service.users().messages().list.return_value.execute = MagicMock(
            return_value=list_result
        )

        from googleapiclient.errors import HttpError
        mock_resp = MagicMock()
        mock_resp.status = 429
        mock_resp.reason = "Too Many Requests"
        error_429 = HttpError(mock_resp, b'{"error": {"message": "Rate limit"}}')

        call_count = [0]

        class MockBatch:
            def __init__(self, callback):
                self.callback = callback
                self.requests = []
            def add(self, request, **kwargs):
                self.requests.append(request)
            def execute(self):
                call_count[0] += 1
                for i, _req in enumerate(self.requests):
                    if call_count[0] == 1 and i == 1:
                        # msg2 gets 429 on first attempt
                        self.callback(str(i), None, error_429)
                    else:
                        idx = i if call_count[0] == 1 else 1  # retry batch has msg2
                        msg_id = ["msg1", "msg2", "msg3"][idx] if call_count[0] == 1 else "msg2"
                        resp = _make_api_message(
                            msg_id=msg_id,
                            subject=f"Subject {msg_id}",
                            internal_date=str(1771578900000 - i * 60000),
                            format="metadata",
                        )
                        self.callback(str(i), resp, None)

        adapter._service.new_batch_http_request = lambda callback: MockBatch(callback)

        results = await adapter.list_messages("newer_than:1d")

        assert len(results) == 3  # All 3 returned, none silently dropped
        assert call_count[0] == 2  # Original batch + 1 retry batch
        result_ids = {r["raw_id"] for r in results}
        assert result_ids == {"msg1", "msg2", "msg3"}


# ---------------------------------------------------------------------------
# Integration tests — require valid Google credentials
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGmailAdapterIntegration:
    """Integration tests that connect to real Gmail API.

    These are skipped by default.  Run with::

        pytest -m integration tests/test_gmail_adapter.py -v

    Prerequisites:
    - Valid Google OAuth credentials (run ``ts4k auth <prefix>`` first)
    - Set env var TS4K_TEST_EMAIL to the Google email to use
    """

    @pytest.fixture
    def adapter(self):
        import os

        email = os.environ.get("TS4K_TEST_EMAIL", "user@gmail.com")
        return GmailAdapter(GmailAdapterConfig(user_email=email))

    @pytest.mark.asyncio
    async def test_connect_and_list(self, adapter):
        async with adapter:
            results = await adapter.list_messages("newer_than:1d", count=3)
            assert isinstance(results, list)
            if results:
                assert results[0]["id"].startswith("g:")
                assert results[0]["from"]  # Has header data.
                assert results[0]["subject"] is not None

    @pytest.mark.asyncio
    async def test_read_message(self, adapter):
        async with adapter:
            results = await adapter.list_messages("newer_than:7d", count=1)
            if results:
                msg = await adapter.read_message(results[0]["id"])
                assert "body" in msg
                assert msg["from"]

    @pytest.mark.asyncio
    async def test_read_thread(self, adapter):
        async with adapter:
            results = await adapter.list_messages("newer_than:7d", count=1)
            if results:
                thread = await adapter.read_thread(results[0]["thread_id"])
                assert "messages" in thread
                assert thread["message_count"] >= 1

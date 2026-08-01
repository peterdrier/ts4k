"""Tests for the WhatsApp adapter's message parsing (issue #11).

Covers the FROM/SUBJECT mapping for regular group messages and DMs, and
the filtering of system/membership-change notifications (which arrive
with swapped sender/chat-name fields from the upstream bridge).
"""

import json

from ts4k.adapters.whatsapp import parse_list_messages_response


def _ndjson(*msgs: dict) -> str:
    return "\n".join(json.dumps(m) for m in msgs)


def test_regular_group_message_maps_sender_to_from_and_group_to_subject():
    text = _ndjson({
        "id": "ABC123",
        "timestamp": "2026-08-01 10:00:00",
        "sender_jid": "15551234567@s.whatsapp.net",
        "sender_name": "Guy",
        "chat_jid": "120363000000000000@g.us",
        "chat_name": "SC26 core group",
        "content": "hey everyone",
        "is_from_me": False,
    })

    result = parse_list_messages_response(text, "w")

    assert len(result) == 1
    assert result[0]["from"] == "Guy"
    assert result[0]["subject"] == "SC26 core group"
    assert result[0]["body"] == "hey everyone"


def test_dm_maps_sender_to_from_and_contact_to_subject():
    text = _ndjson({
        "id": "DEF456",
        "timestamp": "2026-08-01 11:00:00",
        "sender_jid": "15559876543@s.whatsapp.net",
        "sender_name": "Mom",
        "chat_jid": "15559876543@s.whatsapp.net",
        "chat_name": "Mom",
        "content": "call me later",
        "is_from_me": False,
    })

    result = parse_list_messages_response(text, "w")

    assert len(result) == 1
    assert result[0]["from"] == "Mom"
    assert result[0]["subject"] == "Mom"


def test_membership_change_notification_is_filtered_out():
    # Upstream reports these with the group name in sender_name and the
    # member's name in chat_name (swapped), and no content/media — this
    # is what distinguishes them from real messages.
    text = _ndjson({
        "id": "GHI789",
        "timestamp": "2026-08-01 12:00:00",
        "sender_jid": "120363111111111111@g.us",
        "sender_name": "Book Club \U0001f4d6\U0001f377",
        "chat_jid": "120363111111111111@g.us",
        "chat_name": "Christoph Hartmann",
        "content": "",
        "is_from_me": False,
    })

    result = parse_list_messages_response(text, "w")

    assert result == []


def test_membership_notification_filtered_but_real_message_in_same_batch_kept():
    text = _ndjson(
        {
            "id": "GHI789",
            "timestamp": "2026-08-01 12:00:00",
            "sender_jid": "120363111111111111@g.us",
            "sender_name": "Book Club \U0001f4d6\U0001f377",
            "chat_jid": "120363111111111111@g.us",
            "chat_name": "Christoph Hartmann",
            "content": "",
            "is_from_me": False,
        },
        {
            "id": "ABC123",
            "timestamp": "2026-08-01 10:00:00",
            "sender_jid": "15551234567@s.whatsapp.net",
            "sender_name": "Guy",
            "chat_jid": "120363000000000000@g.us",
            "chat_name": "SC26 core group",
            "content": "hey everyone",
            "is_from_me": False,
        },
    )

    result = parse_list_messages_response(text, "w")

    assert len(result) == 1
    assert result[0]["from"] == "Guy"
    assert result[0]["subject"] == "SC26 core group"


def test_media_message_with_empty_content_is_not_filtered():
    text = _ndjson({
        "id": "JKL012",
        "timestamp": "2026-08-01 13:00:00",
        "sender_jid": "15551234567@s.whatsapp.net",
        "sender_name": "Guy",
        "chat_jid": "120363000000000000@g.us",
        "chat_name": "SC26 core group",
        "content": "",
        "media_type": "image",
        "is_from_me": False,
    })

    result = parse_list_messages_response(text, "w")

    assert len(result) == 1
    assert result[0]["from"] == "Guy"
    assert result[0]["subject"] == "SC26 core group"

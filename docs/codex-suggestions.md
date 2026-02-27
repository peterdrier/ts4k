# Codex Suggestions: High-Value Adapter Expansion

Date: February 24, 2026

## Goal

Grow ts4k usefulness by adding high-usage messaging surfaces with Python-friendly integration paths, while keeping maintenance sane.

## Priority Adapters (Recommended)

1. Slack
- Why: very high workplace message volume and daily usage.
- Python path: `slack_sdk` (official).
- Notes: watch rate-limit policy differences for non-Marketplace apps.

2. Microsoft Teams Chat
- Why: major enterprise chat surface that complements O365 mail.
- Python path: Microsoft Graph via `msgraph-sdk`.
- Notes: reuse your existing Graph auth and token patterns.

3. Telegram
- Why: very large global user base and common personal/work overlap.
- Python path: bot-based (`python-telegram-bot`) or account-based (`Telethon`).
- Notes: choose bot vs account model early because capabilities differ.

## Secondary Adapters

1. Google Chat
- Why: strong fit for Google Workspace users already on Gmail.
- Python path: Google Workspace Chat APIs and client libraries.

2. Discord
- Why: large consumer and community usage.
- Python path: community SDKs (`discord.py`), not first-party Python.

3. Twilio Messaging/Conversations
- Why: one integration can cover SMS and some WhatsApp-style workflows.
- Python path: official Twilio Python SDK.

4. GitHub Notifications
- Why: high value for developer persona; easy “inbox” win.
- Python path: GitHub REST notifications APIs.

## Your Mentioned Channels

1. Instagram
- Feasibility: partial.
- Reality: official messaging APIs are business-oriented and constrained (not a general personal-DM API surface).
- Practical approach: treat as lower priority direct adapter; short-term, capture Instagram alerts via existing email ingestion if that already covers your need.

2. Facebook Messenger
- Feasibility: partial.
- Reality: official APIs are page/business scoped, not broad personal-messenger access.
- Practical approach: likely not worth early investment unless your use case is page/business messaging.

3. Signal
- Feasibility: no stable official API path for this product shape.
- Reality: mostly unofficial/bridge options; operationally brittle and high maintenance risk.
- Practical approach: skip for now unless you accept unsupported integration risk.

## Recommended Sequence

1. Build Slack adapter.
2. Build Teams Chat adapter.
3. Build Telegram adapter.
4. Reassess Instagram/Messenger based on product demand.
5. Keep Signal out of core roadmap for now.

"""Message filter — opt-in skip/keep rules applied in the core pipeline.

Filters are **off by default**.  The CLI passes ``apply=True`` only when
the user requests ``--filter`` / ``-F``.  This keeps the full unfiltered
feed available for triage, labeling, and unsubscribe recommendations.

Usage::

    from ts4k.core.filter import apply_filters
    from ts4k.state.filters import get_config

    config = get_config()
    filtered = apply_filters(messages, config)
"""

from __future__ import annotations

import re
from typing import Any


def apply_filters(
    messages: list[dict],
    config: dict[str, Any],
) -> list[dict]:
    """Apply filter config to a list of message dicts.

    Returns a new list with skipped messages removed.
    Each rule is checked independently — a message is skipped if *any*
    rule matches.
    """
    skip_senders = {s.lower() for s in config.get("skip_senders", [])}
    skip_domains = {d.lower() for d in config.get("skip_domains", [])}
    skip_groups = config.get("skip_groups", False)
    skip_patterns = _compile_patterns(config.get("skip_patterns", []))
    skip_categories = {c.lower() for c in config.get("skip_categories", [])}

    result: list[dict] = []
    for msg in messages:
        if _should_skip(
            msg, skip_senders, skip_domains, skip_groups, skip_patterns,
            skip_categories,
        ):
            continue
        result.append(msg)
    return result


def _compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    """Compile regex patterns, silently dropping invalid ones.

    Optimized: Combines all valid patterns into a single regex using OR (|)
    to avoid looping over multiple regexes during text search. Falls back
    to individual regexes if backreferences or inline flags are detected.
    """
    valid_patterns = []
    for p in patterns:
        try:
            # Test if it compiles cleanly
            re.compile(p, re.IGNORECASE)

            # Backreferences and inline flags break when concatenated inside non-capturing groups
            if "(?" in p or re.search(r"\\[1-9]", p):
                return _fallback_compile(patterns)

            valid_patterns.append(f"(?:{p})")
        except re.error:
            continue

    if not valid_patterns:
        return []

    try:
        return [re.compile("|".join(valid_patterns), re.IGNORECASE)]
    except re.error:
        # Fallback if combined regex is too complex or throws error
        return _fallback_compile(patterns)


def _fallback_compile(patterns: list[str]) -> list[re.Pattern]:
    """Fallback method for compiling regex patterns individually."""
    compiled = []
    for p in patterns:
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue
    return compiled


def _should_skip(
    msg: dict,
    skip_senders: set[str],
    skip_domains: set[str],
    skip_groups: bool,
    skip_patterns: list[re.Pattern],
    skip_categories: set[str] | None = None,
) -> bool:
    """Return True if the message should be filtered out."""
    sender = (msg.get("from") or "").strip().lower()

    # Skip by adapter-assigned category (e.g. GitHub's ci / dependabot)
    if skip_categories:
        category = (msg.get("category") or "").strip().lower()
        if category and category in skip_categories:
            return True

    # Skip by exact sender
    if sender and sender in skip_senders:
        return True

    # Skip by sender domain
    if sender and "@" in sender:
        domain = sender.rsplit("@", 1)[1]
        if domain in skip_domains:
            return True

    # Skip group chats (WhatsApp groups end with @g.us)
    if skip_groups:
        chat_jid = msg.get("chat_jid", "")
        is_group = msg.get("is_group", False)
        if is_group or (chat_jid and chat_jid.endswith("@g.us")):
            return True

    # Skip by pattern match on subject + body
    if skip_patterns:
        text = (msg.get("subject") or "") + " " + (msg.get("body") or "")
        for pattern in skip_patterns:
            if pattern.search(text):
                return True

    return False

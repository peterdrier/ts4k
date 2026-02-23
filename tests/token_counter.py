"""Shared token counting helper using Anthropic's Token Count API."""

import os
import time

_client = None
_DEFAULT_MODEL = "claude-sonnet-4-6"
_MIN_INTERVAL = 0.6  # ~100 RPM


def _get_client():
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        import anthropic
        _client = anthropic.Anthropic()
    return _client


_last_call = 0.0


def count_tokens(text: str, model: str = _DEFAULT_MODEL) -> int | None:
    """Return exact token count for *text*, or None on failure."""
    global _last_call
    client = _get_client()
    if client is None:
        return None
    try:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        resp = client.messages.count_tokens(
            model=model,
            messages=[{"role": "user", "content": text}],
        )
        _last_call = time.monotonic()
        return resp.input_tokens
    except Exception:
        return None

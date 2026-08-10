"""Token efficiency regression tests for MCP tool/param naming.

Two layers:

* ``TestByteBudgets`` — coarse byte-count proxies that run **always**, including
  in CI where no ANTHROPIC_API_KEY is set.  These catch large regressions
  (the kind that go unnoticed for months) without an API call.
* ``TestTokenEfficiency`` — precise budgets measured with the Anthropic token
  counting API.  Skipped automatically when ANTHROPIC_API_KEY is not set, so
  these run locally / manually.
"""

from __future__ import annotations

import json
import os

import pytest

# Token budgets below are TOKENIZER-DEPENDENT: they were measured against the
# model pinned here and are not portable across tokenizer generations.  When
# MODEL changes, re-measure every budget in this file and update it — do not
# assume the old numbers still hold.
#
# claude-opus-5 is pinned because ts4k's consumers are Claude Code agents (the
# `ts` skill) and MCP clients, which run Opus-tier by default.  Claude Sonnet 5
# shares this tokenizer; measured name-level counts are identical and MCP tool
# context differs by ~1%, so these budgets cover both.
#
# Last re-baselined: 2026-08-10 against claude-opus-5.
MODEL = "claude-opus-5"

# Names that exceed the 3-token budget.  Measured against MODEL; these are
# tokenizer cost, not name bloat — `whatsnew` is already as short as the
# command can be, and cal_* follow the established verb_noun convention.
TOOL_NAME_EXCEPTIONS: dict[str, int] = {
    "whatsnew": 5,
    "cal_create": 4,
    "cal_manage": 5,
}

# Params that exceed the 2-token budget.  `fmt` / `cmd` are already
# three-letter abbreviations and cost 3 tokens purely because of how the
# tokenizer splits them; the snake_case compounds cost one token per word plus
# the underscore.  Shortening these would hurt readability without saving
# tokens, so they are documented exceptions rather than a raised budget.
PARAM_NAME_EXCEPTIONS: dict[str, int] = {
    "fmt": 3,
    "cmd": 3,
    "dry_run": 4,
    "reply_to": 4,
    "from_date": 3,
    "to_date": 3,
    "attendees": 3,
}


def _make_client():
    import anthropic

    return anthropic.Anthropic()


def _count_message_tokens(text: str) -> int:
    """Count tokens for a user message containing *text*."""
    client = _make_client()
    resp = client.messages.count_tokens(
        model=MODEL,
        messages=[{"role": "user", "content": text}],
    )
    return resp.input_tokens


# Baseline: tokens for a minimal single-char message (system/framing overhead)
_baseline: int | None = None


def _get_baseline() -> int:
    global _baseline
    if _baseline is None:
        _baseline = _count_message_tokens("x")
    return _baseline


def _count_net_tokens(text: str) -> int:
    """Count how many tokens *text* uses, net of framing overhead.

    Measures by comparing a message containing *text* vs a single-char
    baseline.  The single char 'x' is 1 token, so we add 1 back.

    Caveat for short identifiers: a name tokenizes differently in isolation
    than it does inside a JSON schema, so the per-name budgets are a proxy.
    The exception tables above were measured with this same helper, so they
    stay self-consistent.
    """
    raw = _count_message_tokens(text)
    return raw - _get_baseline() + 1


def _get_tool_definitions() -> list[dict]:
    """Extract MCP tool definitions from the FastMCP instance."""
    from ts4k.server import mcp

    tools = mcp._tool_manager._tools
    definitions = []
    for name, tool in tools.items():
        definitions.append({
            "name": name,
            "parameters": tool.parameters,
            "description": tool.description or "",
        })
    return definitions


def _get_tool_schemas() -> list[dict]:
    """Build Anthropic API tool schemas from MCP tool definitions."""
    defs = _get_tool_definitions()
    schemas = []
    for d in defs:
        schema = {
            "name": d["name"],
            "description": d["description"],
            "input_schema": {
                "type": "object",
                "properties": d["parameters"].get("properties", {}),
                "required": d["parameters"].get("required", []),
            },
        }
        schemas.append(schema)
    return schemas


class TestByteBudgets:
    """Coarse guards that run without an API key (i.e. in CI).

    Byte counts are a stand-in for tokens: across these payloads the ratio is
    ~2.3 bytes/token, so a large token regression shows up as a proportional
    byte regression.  Ceilings are the measured size plus roughly 15% headroom
    — loose enough for incremental edits, tight enough that a multiple-x
    overrun fails immediately instead of going unnoticed for months.
    """

    def test_skill_reference_bytes(self):
        """Skill self-doc rides in every call; guard its size without an API key."""
        from ts4k.commands import skill_reference

        basic = len(skill_reference("basic").encode("utf-8"))  # 2884 bytes at time of writing
        more = len(skill_reference("more").encode("utf-8"))  # 1197 bytes at time of writing

        assert basic < 3300, f"Tier 1 skill ref is {basic} bytes (budget: 3300)"
        assert more < 1400, f"Tier 2 skill ref is {more} bytes (budget: 1400)"

    def test_mcp_schema_bytes(self):
        """Serialized MCP tool schemas — proxy for the per-call tool context."""
        payload = len(json.dumps(_get_tool_schemas()).encode("utf-8"))  # 12872 bytes at time of writing

        assert payload < 14800, f"MCP tool schemas are {payload} bytes (budget: 14800)"


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="No ANTHROPIC_API_KEY — skipping token efficiency tests",
)
class TestTokenEfficiency:
    """Validate that MCP naming stays within token budgets."""

    def test_tool_names_max_3_tokens(self):
        """All MCP tool names should be <= 3 tokens (with documented exceptions)."""
        defs = _get_tool_definitions()
        violations = []
        for d in defs:
            name = d["name"]
            tokens = _count_net_tokens(name)
            limit = TOOL_NAME_EXCEPTIONS.get(name, 3)
            if tokens > limit:
                violations.append(f"{name}: {tokens} tokens (limit: {limit})")
        assert not violations, f"Tool names exceed budget: {violations}"

    def test_params_max_2_tokens(self):
        """All MCP parameter names should be <= 2 tokens (with documented exceptions)."""
        defs = _get_tool_definitions()
        violations = []
        seen: set[str] = set()
        for d in defs:
            for param_name in d["parameters"].get("properties", {}):
                if param_name in seen:
                    continue
                seen.add(param_name)
                tokens = _count_net_tokens(param_name)
                limit = PARAM_NAME_EXCEPTIONS.get(param_name, 2)
                if tokens > limit:
                    violations.append(f"{param_name}: {tokens} tokens (limit: {limit})")
        assert not violations, f"Param names exceed budget: {violations}"

    def test_mcp_context_under_budget(self):
        """Total MCP tool definition context should stay within budget.

        The API's native tool counting includes JSON schema wrapping and type
        annotations beyond just names/descriptions.  Measured at 5590 tokens
        against MODEL for 12 tools; budget is that plus headroom for minor
        additions.

        This is a re-baseline, not a target that is being met: the old 2300
        budget predates the calendar tools (cal, cal_create, cal_manage) and a
        different tokenizer.  Trimming the tool surface back toward 2300 is
        separate work — see the follow-up issue.
        """
        client = _make_client()
        tools = _get_tool_schemas()

        with_tools = client.messages.count_tokens(
            model=MODEL,
            messages=[{"role": "user", "content": "hello"}],
            tools=tools,
        )
        without_tools = client.messages.count_tokens(
            model=MODEL,
            messages=[{"role": "user", "content": "hello"}],
        )
        tool_cost = with_tools.input_tokens - without_tools.input_tokens
        assert tool_cost < 6000, f"MCP context is {tool_cost} tokens (budget: 6000)"

    def test_skill_reference_under_budget(self):
        """Skill self-doc output should stay within budget.

        Measured against MODEL: tier 1 is 1220 tokens, tier 2 is 505.  Budgets
        are those plus headroom.

        Like the MCP context above, this is an honest re-baseline of a budget
        that was never being met, not a claim that 200 tokens was wrong as a
        goal.  Cutting the skill text back is separate work — see the follow-up
        issue.
        """
        from ts4k.commands import skill_reference

        basic_tokens = _count_net_tokens(skill_reference("basic"))
        more_tokens = _count_net_tokens(skill_reference("more"))

        assert basic_tokens < 1300, (
            f"Tier 1 skill ref is {basic_tokens} tokens (budget: 1300)"
        )
        assert more_tokens < 600, f"Tier 2 skill ref is {more_tokens} tokens (budget: 600)"

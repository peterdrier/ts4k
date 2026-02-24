"""Token efficiency regression tests for MCP tool/param naming.

Uses the Anthropic token counting API to verify that tool names, parameter
names, and overall MCP context stay within token budgets.  Skipped
automatically when ANTHROPIC_API_KEY is not set.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="No ANTHROPIC_API_KEY — skipping token efficiency tests",
)

MODEL = "claude-sonnet-4-20250514"

# preload_status is a compound name (4 tokens) — accepted tradeoff for clarity
TOOL_NAME_EXCEPTIONS = {"preload_status": 4}


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


def _count_name_tokens(name: str) -> int:
    """Count how many tokens a name uses, net of framing overhead.

    Measures by comparing a message containing the name vs a single-char
    baseline.  The single char 'x' is 1 token, so we add 1 back.
    """
    raw = _count_message_tokens(name)
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


class TestTokenEfficiency:
    """Validate that MCP naming stays within token budgets."""

    def test_tool_names_max_3_tokens(self):
        """All MCP tool names should be <= 3 tokens (with documented exceptions)."""
        defs = _get_tool_definitions()
        violations = []
        for d in defs:
            name = d["name"]
            tokens = _count_name_tokens(name)
            limit = TOOL_NAME_EXCEPTIONS.get(name, 3)
            if tokens > limit:
                violations.append(f"{name}: {tokens} tokens (limit: {limit})")
        assert not violations, f"Tool names exceed budget: {violations}"

    def test_params_max_2_tokens(self):
        """All MCP parameter names should be <= 2 tokens."""
        defs = _get_tool_definitions()
        violations = []
        seen: set[str] = set()
        for d in defs:
            for param_name in d["parameters"].get("properties", {}):
                if param_name in seen:
                    continue
                seen.add(param_name)
                tokens = _count_name_tokens(param_name)
                if tokens > 2:
                    violations.append(f"{param_name}: {tokens} tokens")
        assert not violations, f"Param names exceed 2 tokens: {violations}"

    def test_mcp_context_under_budget(self):
        """Total MCP tool definition context should stay within budget.

        The API's native tool counting includes JSON schema wrapping and type
        annotations beyond just names/descriptions.  Budget is set based on
        measured baseline after the rename (2705 tokens at time of writing)
        with headroom for minor additions.
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
        assert tool_cost < 3000, f"MCP context is {tool_cost} tokens (budget: 3000)"

    def test_skill_reference_under_200(self):
        """Skill self-doc output should be under 200 tokens."""
        from ts4k.commands import skill_reference

        basic = skill_reference("basic")
        more = skill_reference("more")

        basic_tokens = _count_name_tokens(basic)
        more_tokens = _count_name_tokens(more)

        assert basic_tokens < 200, f"Tier 1 skill ref is {basic_tokens} tokens (budget: 200)"
        assert more_tokens < 200, f"Tier 2 skill ref is {more_tokens} tokens (budget: 200)"

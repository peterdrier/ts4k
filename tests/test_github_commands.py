"""Tests for GitHub provider registration in commands.py and the CLI.

No test here touches the network: adapter construction is inspected, not
connected, and every command path is driven with a mock adapter.
"""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock

import pytest

from ts4k import cli, commands
from ts4k.adapters.github import GitHubAdapter

GITHUB_CFG = {"provider": "github", "token": "test-token-not-real", "level": "modify"}


@pytest.fixture(autouse=True)
def no_ambient_token(monkeypatch):
    """A developer's real GITHUB_TOKEN must not leak into these tests."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)


class TestMakeAdapter:
    def test_builds_github_adapter(self):
        adapter = commands._make_adapter("gh", dict(GITHUB_CFG))
        assert isinstance(adapter, GitHubAdapter)
        assert adapter.source_prefix == "gh"

    def test_level_is_carried_through(self):
        from ts4k.core.levels import AccessLevel
        adapter = commands._make_adapter("gh", dict(GITHUB_CFG))
        assert adapter.access_level == AccessLevel.MODIFY

    def test_defaults_to_readonly(self):
        from ts4k.core.levels import AccessLevel
        cfg = dict(GITHUB_CFG)
        del cfg["level"]
        assert commands._make_adapter("gh", cfg).access_level == AccessLevel.READONLY

    def test_no_token_returns_none(self):
        assert commands._make_adapter("gh", {"provider": "github"}) is None

    def test_token_file_is_enough(self, tmp_path):
        path = tmp_path / "pat"
        path.write_text("test-token-not-real", encoding="utf-8")
        cfg = {"provider": "github", "token_file": str(path)}
        assert isinstance(commands._make_adapter("gh", cfg), GitHubAdapter)

    def test_env_token_is_enough(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "test-token-not-real")
        assert isinstance(
            commands._make_adapter("gh", {"provider": "github"}), GitHubAdapter
        )


class TestResolvePrefixes:
    def test_gh_alias_resolves_to_github_sources(self, monkeypatch):
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"work": dict(GITHUB_CFG)}
        )
        assert commands._resolve_prefixes("gh") == ["work"]

    def test_provider_name_resolves(self, monkeypatch):
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"work": dict(GITHUB_CFG)}
        )
        assert commands._resolve_prefixes("github") == ["work"]

    def test_exact_prefix_wins_over_alias(self, monkeypatch):
        monkeypatch.setattr(
            "ts4k.state.sources.list_all",
            lambda: {"gh": dict(GITHUB_CFG), "work": dict(GITHUB_CFG)},
        )
        assert commands._resolve_prefixes("gh") == ["gh"]


class TestTokenHealth:
    def test_configured_token_is_ok(self):
        health = commands.check_token_health("gh", dict(GITHUB_CFG))
        assert health.status == "ok"

    def test_missing_token_needs_auth(self):
        health = commands.check_token_health("gh", {"provider": "github"})
        assert health.status == "auth"
        assert "ts4k src add gh github" in health.detail

    def test_detail_never_echoes_the_token(self):
        health = commands.check_token_health("gh", dict(GITHUB_CFG))
        assert "test-token-not-real" not in health.detail


class TestWhatsnewFanOut:
    """GitHub rows flow through the shared pipeline like any other source."""

    @pytest.mark.asyncio
    async def test_notifications_reach_the_listing(self, monkeypatch, ts4k_config):
        adapter = AsyncMock()
        adapter.__aenter__.return_value = adapter
        adapter.__aexit__.return_value = None
        adapter.whatsnew.return_value = [
            {
                "id": "gh:1",
                "source": "gh",
                "thread_id": "gh:peterdrier/ts4k#42",
                "from": "peterdrier/ts4k",
                "subject": "[review] Add GitHub adapter",
                "date": "2026-08-09T14:30:00Z",
                "category": "review",
            },
            {
                "id": "gh:2",
                "source": "gh",
                "thread_id": "",
                "from": "peterdrier/ts4k",
                "subject": "[ci] build failed",
                "date": "2026-08-09T13:00:00Z",
                "category": "ci",
            },
        ]
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"gh": dict(GITHUB_CFG)}
        )
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: adapter)

        result = await commands.whatsnew(key="dev")
        assert "[review] Add GitHub adapter" in result.output
        assert "[ci] build failed" in result.output

    @pytest.mark.asyncio
    async def test_ci_is_filterable_without_losing_reviews(
        self, monkeypatch, ts4k_config
    ):
        from ts4k.state import filters
        filters.add_category("ci")

        adapter = AsyncMock()
        adapter.__aenter__.return_value = adapter
        adapter.__aexit__.return_value = None
        adapter.whatsnew.return_value = [
            {
                "id": "gh:1", "source": "gh", "from": "peterdrier/ts4k",
                "subject": "[review] Add GitHub adapter", "category": "review",
                "date": "2026-08-09T14:30:00Z",
            },
            {
                "id": "gh:2", "source": "gh", "from": "peterdrier/ts4k",
                "subject": "[ci] build failed", "category": "ci",
                "date": "2026-08-09T13:00:00Z",
            },
        ]
        monkeypatch.setattr(
            "ts4k.state.sources.list_all", lambda: {"gh": dict(GITHUB_CFG)}
        )
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: adapter)

        result = await commands.whatsnew(key="dev", filter=True)
        assert "[review] Add GitHub adapter" in result.output
        assert "[ci] build failed" not in result.output

    @pytest.mark.asyncio
    async def test_a_broken_github_source_does_not_break_the_others(
        self, monkeypatch, ts4k_config
    ):
        broken = AsyncMock()
        broken.__aenter__.side_effect = RuntimeError("GitHub rejected the token (401)")

        working = AsyncMock()
        working.__aenter__.return_value = working
        working.__aexit__.return_value = None
        working.whatsnew.return_value = [{
            "id": "o:1", "source": "o", "from": "alice@contoso.com",
            "subject": "Budget", "date": "2026-08-09T12:00:00Z",
        }]

        monkeypatch.setattr(
            "ts4k.state.sources.list_all",
            lambda: {"gh": dict(GITHUB_CFG), "o": {"provider": "o365"}},
        )
        monkeypatch.setattr(
            commands, "_make_adapter",
            lambda p, c: broken if p == "gh" else working,
        )

        result = await commands.whatsnew(key="dev")
        assert result.error is None
        assert "Budget" in result.output


class TestManageMarkRead:
    @pytest.mark.asyncio
    async def test_manage_read_calls_mark_read(self, monkeypatch):
        adapter = AsyncMock()
        adapter.__aenter__.return_value = adapter
        adapter.__aexit__.return_value = None
        adapter.mark_read.return_value = {"id": "gh:1", "status": "marked_read"}

        monkeypatch.setattr("ts4k.state.sources.get", lambda p: dict(GITHUB_CFG))
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: adapter)

        out = await commands.manage_message("read", "gh:1")
        adapter.mark_read.assert_awaited_once_with("gh:1")
        assert out == "gh:1: marked_read"

    @pytest.mark.asyncio
    async def test_dry_run_does_not_touch_github(self, monkeypatch):
        adapter = AsyncMock()
        monkeypatch.setattr("ts4k.state.sources.get", lambda p: dict(GITHUB_CFG))
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: adapter)

        out = await commands.manage_message("read", "gh:1", dry_run=True)
        assert out == "gh:1: would read"
        adapter.mark_read.assert_not_called()

    @pytest.mark.asyncio
    async def test_readonly_source_reports_the_permission_error(self, monkeypatch):
        adapter = AsyncMock()
        adapter.__aenter__.return_value = adapter
        adapter.__aexit__.return_value = None
        adapter.mark_read.side_effect = PermissionError("requires level='modify'")

        monkeypatch.setattr(
            "ts4k.state.sources.get", lambda p: {"provider": "github", "token": "x"}
        )
        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: adapter)

        out = await commands.manage_message("read", "gh:1")
        assert "requires level='modify'" in out


class TestSrcAddCli:
    def _add(self, params: list[str]) -> argparse.Namespace:
        return argparse.Namespace(
            action="add", prefix="gh", provider="github", params=params
        )

    def test_stores_token_file(self, ts4k_config, capsys, tmp_path):
        path = tmp_path / "pat"
        path.write_text("test-token-not-real", encoding="utf-8")
        cli._cmd_sources(self._add([f"token_file={path}"]))

        from ts4k.state import sources
        assert sources.get("gh") == {
            "provider": "github", "token_file": str(path)
        }

    def test_token_value_is_stored_but_never_printed(self, ts4k_config, capsys):
        cli._cmd_sources(self._add(["token=ghp_secretvalue"]))

        from ts4k.state import sources
        assert sources.get("gh")["token"] == "ghp_secretvalue"
        out = capsys.readouterr().out
        assert "ghp_secretvalue" not in out
        assert "<redacted>" in out

    def test_missing_token_is_refused(self, ts4k_config, capsys):
        cli._cmd_sources(self._add([]))

        from ts4k.state import sources
        assert sources.get("gh") is None
        assert "personal access token is required" in capsys.readouterr().out

    def test_level_modify_is_stored(self, ts4k_config):
        cli._cmd_sources(self._add(["token=ghp_x", "level=modify"]))

        from ts4k.state import sources
        assert sources.get("gh")["level"] == "modify"


class TestFilterCli:
    def test_add_category(self, ts4k_config, capsys):
        cli._cmd_filter(argparse.Namespace(action="add-category", value="ci"))
        assert "skip_categories: ci" in capsys.readouterr().out

        from ts4k.state import filters
        assert filters.get_config()["skip_categories"] == ["ci"]

    def test_rm_category(self, ts4k_config, capsys):
        from ts4k.state import filters
        filters.add_category("ci")
        cli._cmd_filter(argparse.Namespace(action="rm-category", value="ci"))
        assert "skip_categories: (empty)" in capsys.readouterr().out

    def test_show_lists_categories(self, ts4k_config, capsys):
        from ts4k.state import filters
        filters.add_category("dependabot")
        cli._cmd_filter(argparse.Namespace(action="show", value=None))
        assert "skip_categories: dependabot" in capsys.readouterr().out

    def test_parser_accepts_category_actions(self):
        parser = cli._build_parser()
        args = parser.parse_args(["filter", "add-category", "ci"])
        assert args.action == "add-category"
        assert args.value == "ci"


class TestHelpText:
    def test_llm_help_documents_github_setup(self, ts4k_config):
        text = commands.llm_help()
        assert "ts4k src add gh github" in text
        assert "filter add-category ci" in text

    def test_skill_more_lists_category_actions(self):
        more = commands.skill_reference("more")
        assert "add-category" in more
        assert "dependabot" in more

    def test_skill_template_mentions_github(self):
        assert "GitHub" in commands.skill_template()


class TestNoLiveCalls:
    @pytest.mark.asyncio
    async def test_mark_read_without_a_client_cannot_reach_github(self):
        """An unconnected adapter fails loudly rather than dialling out."""
        from ts4k.adapters.github import GitHubAdapterConfig
        adapter = GitHubAdapter(GitHubAdapterConfig(token="x", level="modify"))
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.mark_read("gh:1")

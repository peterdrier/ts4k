# Whatsnew/Updates Split — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the current `updates`/`whatsnew` command into two: `updates` (stateless, time-based) and `whatsnew <key>` (stateful, keyed watermarks).

**Architecture:** Extract shared fetch layer from current `whatsnew()` in `commands.py`. New keyed watermark module in `state/`. Two thin command functions calling the shared layer. `CommandResult` gains `has_more`/`remaining` fields.

**Tech Stack:** Python 3.12+, existing ts4k patterns (pytest, monkeypatch, tmp_path for state isolation).

**Design doc:** `docs/plans/2026-03-08-whatsnew-updates-split.md`

---

### Task 1: Keyed watermark module

**Files:**
- Create: `src/ts4k/state/keyed_watermarks.py`
- Create: `tests/test_keyed_watermarks.py`

**Step 1: Write the failing tests**

```python
"""Tests for ts4k.state.keyed_watermarks."""

import json

import pytest

from ts4k.state import keyed_watermarks as kwm


@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    """Point keyed watermarks at a temp directory."""
    monkeypatch.setattr(kwm, "_CONFIG_DIR", tmp_path)
    return tmp_path


class TestGet:
    def test_returns_empty_when_no_file(self):
        assert kwm.get_all("life") == {}

    def test_returns_stored_values(self, tmp_config_dir):
        wm_dir = tmp_config_dir / "watermarks"
        wm_dir.mkdir()
        (wm_dir / "life.json").write_text('{"g": "2026-03-01T00:00:00Z", "o": "2026-03-02T00:00:00Z"}')
        result = kwm.get_all("life")
        assert result == {"g": "2026-03-01T00:00:00Z", "o": "2026-03-02T00:00:00Z"}

    def test_get_single_source(self, tmp_config_dir):
        wm_dir = tmp_config_dir / "watermarks"
        wm_dir.mkdir()
        (wm_dir / "life.json").write_text('{"g": "2026-03-01T00:00:00Z"}')
        assert kwm.get("life", "g") == "2026-03-01T00:00:00Z"
        assert kwm.get("life", "w") is None


class TestUpdate:
    def test_creates_dir_and_file(self, tmp_config_dir):
        kwm.update("life", {"g": "2026-03-08T12:00:00Z"})
        data = json.loads((tmp_config_dir / "watermarks" / "life.json").read_text())
        assert data == {"g": "2026-03-08T12:00:00Z"}

    def test_merges_with_existing(self, tmp_config_dir):
        kwm.update("life", {"g": "2026-03-01T00:00:00Z"})
        kwm.update("life", {"o": "2026-03-02T00:00:00Z"})
        result = kwm.get_all("life")
        assert result == {"g": "2026-03-01T00:00:00Z", "o": "2026-03-02T00:00:00Z"}

    def test_overwrites_existing_source(self, tmp_config_dir):
        kwm.update("life", {"g": "2026-03-01T00:00:00Z"})
        kwm.update("life", {"g": "2026-03-08T00:00:00Z"})
        assert kwm.get("life", "g") == "2026-03-08T00:00:00Z"

    def test_different_keys_independent(self, tmp_config_dir):
        kwm.update("life", {"g": "2026-03-01T00:00:00Z"})
        kwm.update("peter", {"g": "2026-03-05T00:00:00Z"})
        assert kwm.get("life", "g") == "2026-03-01T00:00:00Z"
        assert kwm.get("peter", "g") == "2026-03-05T00:00:00Z"


class TestListKeys:
    def test_empty_when_no_dir(self):
        assert kwm.list_keys() == []

    def test_returns_key_names(self, tmp_config_dir):
        wm_dir = tmp_config_dir / "watermarks"
        wm_dir.mkdir()
        (wm_dir / "life.json").write_text('{"g": "2026-03-01T00:00:00Z"}')
        (wm_dir / "peter.json").write_text('{"g": "2026-03-05T00:00:00Z"}')
        assert sorted(kwm.list_keys()) == ["life", "peter"]


class TestCorruptFile:
    def test_handles_invalid_json(self, tmp_config_dir):
        wm_dir = tmp_config_dir / "watermarks"
        wm_dir.mkdir()
        (wm_dir / "life.json").write_text("not json")
        assert kwm.get_all("life") == {}
        kwm.update("life", {"g": "2026-03-01T00:00:00Z"})
        assert kwm.get("life", "g") == "2026-03-01T00:00:00Z"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_keyed_watermarks.py -v`
Expected: FAIL — module `ts4k.state.keyed_watermarks` does not exist

**Step 3: Write the implementation**

```python
"""Keyed watermark tracking — per-key, per-source last-seen timestamps.

Each key (e.g. "life", "peter") gets its own JSON file under
``~/.config/ts4k/watermarks/<key>.json``.  File-per-key avoids contention
between concurrent whatsnew calls with different keys.

File format::

    {
        "g": "2026-03-08T12:00:14Z",
        "o": "2026-03-08T01:19:33Z"
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_CONFIG_DIR = Path(os.environ.get("TS4K_CONFIG_DIR", "~/.config/ts4k")).expanduser()


def _wm_dir() -> Path:
    return _CONFIG_DIR / "watermarks"


def _key_file(key: str) -> Path:
    return _wm_dir() / f"{key}.json"


def _load(key: str) -> dict[str, str]:
    f = _key_file(key)
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_all(key: str) -> dict[str, str]:
    """Return all per-source timestamps for *key*."""
    return _load(key)


def get(key: str, source: str) -> str | None:
    """Return the timestamp for *source* within *key*, or None."""
    return _load(key).get(source)


def update(key: str, timestamps: dict[str, str]) -> None:
    """Merge *timestamps* into *key*'s watermark file."""
    from ts4k.state._io import safe_write_json

    data = _load(key)
    data.update(timestamps)
    d = _wm_dir()
    d.mkdir(parents=True, exist_ok=True)
    safe_write_json(_key_file(key), data)


def list_keys() -> list[str]:
    """Return all watermark key names."""
    d = _wm_dir()
    if not d.is_dir():
        return []
    return sorted(f.stem for f in d.glob("*.json"))
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_keyed_watermarks.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/ts4k/state/keyed_watermarks.py tests/test_keyed_watermarks.py
git commit -m "Add keyed watermark module — per-key, per-source tracking"
```

---

### Task 2: Add has_more/remaining to CommandResult

**Files:**
- Modify: `src/ts4k/commands.py:45-51` (CommandResult dataclass)
- Modify: `tests/test_commands.py`

**Step 1: Write the failing test**

Add to `tests/test_commands.py`:

```python
class TestCommandResult:
    def test_defaults(self):
        r = commands.CommandResult()
        assert r.has_more is False
        assert r.remaining == 0

    def test_has_more(self):
        r = commands.CommandResult(output="data", has_more=True, remaining=15)
        assert r.has_more is True
        assert r.remaining == 15
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_commands.py::TestCommandResult -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'has_more'`

**Step 3: Add fields to CommandResult**

In `src/ts4k/commands.py`, change the `CommandResult` dataclass (line 45-51):

```python
@dataclass
class CommandResult:
    """Return type for commands that process messages."""

    output: str = ""
    messages_processed: int = 0
    error: str | None = None
    ref_map: dict[str, int] | None = None  # {full_id: ref_num}
    has_more: bool = False
    remaining: int = 0
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_commands.py::TestCommandResult -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ts4k/commands.py tests/test_commands.py
git commit -m "Add has_more/remaining fields to CommandResult"
```

---

### Task 3: Extract shared fetch layer

**Files:**
- Modify: `src/ts4k/commands.py:283-377`
- Create: `tests/test_fetch_messages.py`

**Step 1: Write the failing test**

```python
"""Tests for the shared _fetch_messages layer."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ts4k import commands
from ts4k.commands import CommandResult


@pytest.fixture(autouse=True)
def mock_sources(tmp_path, monkeypatch):
    """Set up minimal source config and temp state dir."""
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))

    from ts4k.state import sources, stats, cache

    monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(stats, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(stats, "_STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr(cache, "_CONFIG_DIR", tmp_path)

    cfg = {
        "g": {"provider": "gmail", "email": "test@gmail.com"},
        "o": {"provider": "o365", "client_id": "fake", "tenant_id": "common"},
    }
    (tmp_path / "sources.json").write_text(json.dumps(cfg))

    return tmp_path


def _make_fake_messages(prefix: str, count: int, base_hour: int = 10) -> list[dict]:
    """Generate fake normalized message dicts."""
    msgs = []
    for i in range(count):
        msgs.append({
            "id": f"{prefix}:msg{i}",
            "source": prefix,
            "thread_id": f"{prefix}:thread{i}",
            "from": f"sender{i}@test.com",
            "subject": f"Subject {i}",
            "date": f"2026-03-08T{base_hour + i:02d}:00:00Z",
            "body": f"Body {i}",
        })
    return msgs


class TestFetchMessages:
    @pytest.mark.asyncio
    async def test_returns_command_result(self, monkeypatch):
        """Verify _fetch_messages returns a CommandResult with output."""
        async def fake_fetch(prefix, cfg, since, count):
            return _make_fake_messages(prefix, 3)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands._fetch_messages(
            since={"g": "2026-03-08T00:00:00Z"},
            count=20,
        )
        assert isinstance(result, CommandResult)
        assert result.output
        assert result.messages_processed == 3
        assert result.has_more is False
        assert result.remaining == 0

    @pytest.mark.asyncio
    async def test_truncation_sets_has_more(self, monkeypatch):
        """When total fetched > count, has_more=True with remaining count."""
        async def fake_fetch(prefix, cfg, since, count):
            return _make_fake_messages(prefix, 15, base_hour=1)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands._fetch_messages(
            since={"g": "2026-03-08T00:00:00Z", "o": "2026-03-08T00:00:00Z"},
            count=20,
        )
        # 15 per source = 30 total, truncated to 20
        assert result.has_more is True
        assert result.remaining == 10
        assert result.messages_processed == 20

    @pytest.mark.asyncio
    async def test_returns_messages_sorted_newest_first(self, monkeypatch):
        """Messages from multiple sources are sorted by date descending."""
        async def fake_fetch(prefix, cfg, since, count):
            if prefix == "g":
                return _make_fake_messages("g", 2, base_hour=14)
            return _make_fake_messages("o", 2, base_hour=10)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands._fetch_messages(
            since={"g": "2026-03-08T00:00:00Z", "o": "2026-03-08T00:00:00Z"},
            count=20,
        )
        # Gmail messages (14:xx, 15:xx) should appear before O365 (10:xx, 11:xx)
        assert "g:msg" in result.output.split("\n")[1]  # first data line after header

    @pytest.mark.asyncio
    async def test_no_messages_returns_error(self, monkeypatch):
        async def fake_fetch(prefix, cfg, since, count):
            return []

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands._fetch_messages(
            since={"g": "2026-03-08T00:00:00Z"},
            count=20,
        )
        assert result.error == "No new messages."
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_fetch_messages.py -v`
Expected: FAIL — `commands._fetch_messages` does not exist

**Step 3: Refactor commands.py**

Rename `_fetch_whatsnew_for_source` to `_fetch_for_source` and change its signature to accept a resolved UTC timestamp (no watermark lookups):

```python
async def _fetch_for_source(
    prefix: str, cfg: dict[str, Any], since: str | None, count: int
) -> list[dict]:
    """Fetch messages from a single source since a UTC timestamp.

    *since* must be a resolved ISO-8601 UTC timestamp or None (adapter default).
    No watermark lookups — the caller resolves timestamps.
    """
    adapter = _make_adapter(prefix, cfg)
    if adapter is None:
        return []

    provider = cfg.get("provider", "").lower()
    try:
        async with adapter:
            if provider == "gmail":
                # Convert UTC timestamp to Gmail query format
                query = _utc_to_gmail_query(since)
                listing = await adapter.list_messages(query=query, count=count)
            else:
                listing = await adapter.whatsnew(since=since)

            if not listing:
                return []
            messages = []
            for entry in listing[:count]:
                msg = _normalize_message(entry)
                msg.setdefault("source", prefix)
                cache.store_message(msg.get("id", ""), msg)
                messages.append(msg)
            return messages

    except Exception as exc:
        logger.warning("[%s] adapter failed: %s", prefix, exc)
        return []
```

Add a helper to convert UTC ISO timestamps to Gmail query (replacing the watermark-aware `_since_to_gmail_query`):

```python
def _utc_to_gmail_query(since: str | None) -> str:
    """Convert a UTC ISO timestamp to a Gmail search query fragment."""
    if since is None:
        return "newer_than:1d"
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return f"after:{int(dt.timestamp())}"
    except ValueError:
        return "newer_than:1d"
```

Add a helper to resolve relative `--since` to UTC (used by `updates`):

```python
def _resolve_since_to_utc(since: str | None) -> str | None:
    """Resolve a --since value (relative or absolute) to UTC ISO timestamp.

    Returns None for 'all' (no time bound).
    """
    if since is None:
        # Default to 1 day
        dt = datetime.now(timezone.utc) - timedelta(days=1)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if since.lower() == "all":
        return None

    # Relative: Nd or Nh
    if len(since) >= 2 and since[-1] in ("d", "h") and since[:-1].isdigit():
        n = int(since[:-1])
        delta = timedelta(days=n) if since[-1] == "d" else timedelta(hours=n)
        dt = datetime.now(timezone.utc) - delta
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Assume ISO timestamp
    return since
```

Extract the shared fetch layer:

```python
async def _fetch_messages(
    since: dict[str, str],
    count: int = 20,
    source: str | None = None,
    fmt: str = "pipe",
    filter: bool = False,
    ref_table: RefTable | None = None,
) -> CommandResult:
    """Shared fetch layer — parallel fetch, collate, sort, truncate, format.

    *since* maps source prefix to resolved UTC ISO timestamp (or None for no bound).
    Pure: no watermark reads or writes.
    """
    active_prefixes = list(since.keys())
    if source:
        resolved = _resolve_prefixes(source)
        active_prefixes = [p for p in active_prefixes if p in resolved]

    all_cfg = _ensure_sources()

    tasks: list[asyncio.Task] = []
    task_prefixes: list[str] = []

    for prefix in active_prefixes:
        cfg = all_cfg.get(prefix)
        if cfg:
            tasks.append(
                asyncio.create_task(
                    _fetch_for_source(prefix, cfg, since.get(prefix), count)
                )
            )
            task_prefixes.append(prefix)

    if not tasks:
        return CommandResult(error="No sources configured. Run: ts4k src add <prefix> <provider> ...")

    results = await asyncio.gather(*tasks)

    all_messages: list[dict] = []
    for msgs in results:
        all_messages.extend(msgs)

    all_messages.sort(key=lambda m: m.get("date", ""), reverse=True)

    total_fetched = len(all_messages)
    all_messages = all_messages[:count]

    if filter:
        all_messages = apply_filters(all_messages, filters.get_config())

    if not all_messages:
        return CommandResult(error="No new messages.")

    has_more = total_fetched > count
    remaining = total_fetched - len(all_messages) if has_more else 0

    ref_map = ref_table.assign(all_messages) if ref_table else None
    output = format_listing(all_messages, fmt=fmt, ref_map=ref_map)

    if has_more:
        output += f"\n--- {remaining} more messages available ---"

    _record_stats("wn", all_messages, output)

    return CommandResult(
        output=output,
        messages_processed=len(all_messages),
        ref_map=ref_map,
        has_more=has_more,
        remaining=remaining,
    )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_fetch_messages.py -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `uv run pytest tests/ -x -q`
Expected: All existing tests still pass (some may need minor fixups if they reference `_fetch_whatsnew_for_source`)

**Step 6: Commit**

```bash
git add src/ts4k/commands.py tests/test_fetch_messages.py
git commit -m "Extract shared _fetch_messages layer from whatsnew"
```

---

### Task 4: Rewrite updates command (stateless)

**Files:**
- Modify: `src/ts4k/commands.py` (replace `whatsnew()` with `updates()`)
- Modify: `src/ts4k/cli.py:624-633` (parser + handler)
- Modify: `src/ts4k/server.py:51-79` (MCP tool)
- Modify: `tests/test_commands.py`

**Step 1: Write the failing test**

Add to `tests/test_commands.py` (or a new file `tests/test_updates.py`):

```python
class TestUpdates:
    @pytest.mark.asyncio
    async def test_calls_fetch_messages_with_resolved_since(self, monkeypatch, tmp_path):
        """updates resolves --since to UTC and passes to _fetch_messages."""
        from ts4k.state import sources
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
        (tmp_path / "sources.json").write_text('{"g": {"provider": "gmail", "email": "t@t.com"}}')

        captured = {}

        async def fake_fetch(**kwargs):
            captured.update(kwargs)
            return CommandResult(output="ok", messages_processed=1)

        monkeypatch.setattr(commands, "_fetch_messages", fake_fetch)

        await commands.updates(since="2d", count=10)
        # since should be a dict with resolved UTC timestamps, not "2d"
        assert "g" in captured["since"]
        assert "T" in captured["since"]["g"]  # ISO format
        assert captured["count"] == 10

    @pytest.mark.asyncio
    async def test_since_all_passes_none(self, monkeypatch, tmp_path):
        """--since all means no time bound (None values)."""
        from ts4k.state import sources
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
        (tmp_path / "sources.json").write_text('{"g": {"provider": "gmail", "email": "t@t.com"}}')

        captured = {}

        async def fake_fetch(**kwargs):
            captured.update(kwargs)
            return CommandResult(output="ok", messages_processed=1)

        monkeypatch.setattr(commands, "_fetch_messages", fake_fetch)

        await commands.updates(since="all", count=20)
        assert captured["since"]["g"] is None

    @pytest.mark.asyncio
    async def test_does_not_touch_watermarks(self, monkeypatch, tmp_path):
        """updates must not read or write any watermark files."""
        from ts4k.state import sources, keyed_watermarks as kwm
        monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr(kwm, "_CONFIG_DIR", tmp_path)
        (tmp_path / "sources.json").write_text('{"g": {"provider": "gmail", "email": "t@t.com"}}')

        async def fake_fetch(**kwargs):
            return CommandResult(output="ok", messages_processed=1)

        monkeypatch.setattr(commands, "_fetch_messages", fake_fetch)

        await commands.updates(since="1d")
        # No watermark directory should exist
        assert not (tmp_path / "watermarks").exists()
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_updates.py -v` (or the relevant test class)
Expected: FAIL — `commands.updates` does not exist

**Step 3: Write the updates command**

In `src/ts4k/commands.py`, replace the old `whatsnew()` function:

```python
async def updates(
    source: str | None = None,
    since: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    filter: bool = False,
    ref_table: RefTable | None = None,
) -> CommandResult:
    """Fetch messages by time range. Stateless — no watermarks."""
    active_prefixes = _resolve_prefixes(source)
    resolved_ts = _resolve_since_to_utc(since)

    since_map = {p: resolved_ts for p in active_prefixes}

    return await _fetch_messages(
        since=since_map,
        count=count,
        fmt=fmt,
        filter=filter,
        ref_table=ref_table,
    )
```

Update CLI handler `_cmd_whatsnew` → `_cmd_updates` in `src/ts4k/cli.py`:

```python
async def _cmd_updates(args: argparse.Namespace) -> None:
    refs = _new_ref_table()
    result = await commands.updates(
        source=getattr(args, "source", None),
        since=getattr(args, "since", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        filter=getattr(args, "filter", False),
        ref_table=refs,
    )
    if result.error:
        print(result.error)
        return
    refs.save(_refs_path())
    print(result.output)
```

Update CLI parser (line ~627): change primary name to `updates`, alias to `u`, remove `whatsnew`/`wn` aliases:

```python
up = subparsers.add_parser("updates", aliases=["u"], help="Fetch messages by time range")
up.add_argument("--since", help="Time range: 2d, 6h, ISO timestamp, or all")
up.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
up.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all")
_add_common_args(up)
up.set_defaults(func=_cmd_updates)
```

Update MCP server `src/ts4k/server.py` — rename tool and remove watermark reference from docstring:

```python
@mcp.tool()
async def updates(
    source: str = "all",
    since: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    filter: bool = False,
) -> str:
    """Fetch messages by time range (stateless, no watermarks).

    Listings use short refs (#1, #2, ...) — pass these to get/thread.

    Args:
        source: Source prefix (e.g. "g"), provider name ("gmail"), or "all".
        since: Time range — "2d", "7d", ISO timestamp, "all", or omit for 1d default.
        count: Maximum messages to return (default 20).
        fmt: Output format — "pipe" (default, most compact), "json", or "xml".
        filter: Apply configured skip filters (default off).
    """
    result = await commands.updates(
        source=source, since=since, count=count, fmt=fmt,
        filter=filter, ref_table=_refs,
    )
    if result.error:
        return result.error
    return result.output
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ -x -q`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/ts4k/commands.py src/ts4k/cli.py src/ts4k/server.py tests/
git commit -m "Rewrite updates command — stateless, no watermarks"
```

---

### Task 5: Add whatsnew command (keyed watermarks)

**Files:**
- Modify: `src/ts4k/commands.py` (add `whatsnew()`)
- Modify: `src/ts4k/cli.py` (add parser + handler)
- Modify: `src/ts4k/server.py` (add MCP tool)
- Create: `tests/test_whatsnew.py`

**Step 1: Write the failing tests**

```python
"""Tests for the whatsnew command with keyed watermarks."""

from __future__ import annotations

import json

import pytest

from ts4k import commands
from ts4k.commands import CommandResult


@pytest.fixture(autouse=True)
def mock_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))

    from ts4k.state import sources, stats, cache, keyed_watermarks as kwm

    monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(stats, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(stats, "_STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr(cache, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(kwm, "_CONFIG_DIR", tmp_path)

    cfg = {"g": {"provider": "gmail", "email": "t@t.com"}}
    (tmp_path / "sources.json").write_text(json.dumps(cfg))

    return tmp_path


class TestWhatsnew:
    @pytest.mark.asyncio
    async def test_first_run_defaults_to_7d(self, monkeypatch, mock_env):
        """First whatsnew call with no watermarks defaults to 7 days back."""
        captured = {}

        async def fake_fetch(**kwargs):
            captured.update(kwargs)
            return CommandResult(output="ok", messages_processed=1)

        monkeypatch.setattr(commands, "_fetch_messages", fake_fetch)

        await commands.whatsnew(key="life")
        ts = captured["since"]["g"]
        # Should be ~7 days ago, not 1 day
        assert "T" in ts  # ISO format

    @pytest.mark.asyncio
    async def test_saves_watermarks_after_fetch(self, monkeypatch, mock_env):
        """whatsnew saves per-source watermarks from returned messages."""
        from ts4k.state import keyed_watermarks as kwm

        async def fake_fetch(**kwargs):
            return CommandResult(
                output="ok",
                messages_processed=2,
                _fetched_messages=[
                    {"source": "g", "date": "2026-03-08T10:00:00Z"},
                    {"source": "g", "date": "2026-03-08T12:00:00Z"},
                ],
            )

        monkeypatch.setattr(commands, "_fetch_messages", fake_fetch)

        await commands.whatsnew(key="life")
        assert kwm.get("life", "g") == "2026-03-08T12:00:00Z"

    @pytest.mark.asyncio
    async def test_subsequent_run_uses_saved_watermarks(self, monkeypatch, mock_env):
        """Second whatsnew call uses saved watermarks as --since."""
        from ts4k.state import keyed_watermarks as kwm
        kwm.update("life", {"g": "2026-03-07T00:00:00Z"})

        captured = {}

        async def fake_fetch(**kwargs):
            captured.update(kwargs)
            return CommandResult(output="ok", messages_processed=1)

        monkeypatch.setattr(commands, "_fetch_messages", fake_fetch)

        await commands.whatsnew(key="life")
        assert captured["since"]["g"] == "2026-03-07T00:00:00Z"

    @pytest.mark.asyncio
    async def test_independent_keys(self, monkeypatch, mock_env):
        """Different keys maintain independent watermarks."""
        from ts4k.state import keyed_watermarks as kwm
        kwm.update("life", {"g": "2026-03-01T00:00:00Z"})
        kwm.update("peter", {"g": "2026-03-05T00:00:00Z"})

        captured_calls = []

        async def fake_fetch(**kwargs):
            captured_calls.append(dict(kwargs))
            return CommandResult(output="ok", messages_processed=1)

        monkeypatch.setattr(commands, "_fetch_messages", fake_fetch)

        await commands.whatsnew(key="life")
        await commands.whatsnew(key="peter")
        assert captured_calls[0]["since"]["g"] == "2026-03-01T00:00:00Z"
        assert captured_calls[1]["since"]["g"] == "2026-03-05T00:00:00Z"
```

Note: The `_fetched_messages` field on `CommandResult` in the watermark save test above is a design hint — `_fetch_messages` needs to return the raw message list so `whatsnew` can extract per-source newest timestamps. This can be done by adding a `_messages` field to `CommandResult` or by having `_fetch_messages` return a richer result. Adjust test and implementation to match the chosen approach (likely adding `_messages: list[dict]` to `CommandResult`).

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_whatsnew.py -v`
Expected: FAIL — `commands.whatsnew` signature doesn't have `key` parameter

**Step 3: Write the whatsnew command**

In `src/ts4k/commands.py`:

```python
async def whatsnew(
    key: str,
    source: str | None = None,
    count: int = 20,
    fmt: str = "pipe",
    filter: bool = False,
    ref_table: RefTable | None = None,
) -> CommandResult:
    """Fetch new messages using keyed watermarks.

    Loads per-source timestamps from the key's watermark file.
    First run defaults to 7 days back.  After fetch, advances
    watermarks to the newest returned message per source.
    """
    from ts4k.state import keyed_watermarks

    active_prefixes = _resolve_prefixes(source)
    saved = keyed_watermarks.get_all(key)

    # Build since map: use saved watermark or default to 7 days back
    default_since = (
        datetime.now(timezone.utc) - timedelta(days=7)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    since_map = {p: saved.get(p, default_since) for p in active_prefixes}

    result = await _fetch_messages(
        since=since_map,
        count=count,
        fmt=fmt,
        filter=filter,
        ref_table=ref_table,
    )

    # Advance watermarks per source to newest returned message
    if result._messages:
        new_watermarks: dict[str, str] = {}
        for msg in result._messages:
            src = msg.get("source", "")
            date = msg.get("date", "")
            if src and date:
                if date > new_watermarks.get(src, ""):
                    new_watermarks[src] = date
        if new_watermarks:
            keyed_watermarks.update(key, new_watermarks)

    return result
```

Add `_messages` field to `CommandResult`:

```python
@dataclass
class CommandResult:
    output: str = ""
    messages_processed: int = 0
    error: str | None = None
    ref_map: dict[str, int] | None = None
    has_more: bool = False
    remaining: int = 0
    _messages: list[dict] | None = None  # internal: raw messages for watermark tracking
```

Update `_fetch_messages` to populate `_messages` in its return value.

Add CLI parser and handler:

```python
# In _build_parser():
wn = subparsers.add_parser("whatsnew", aliases=["wn"], help="Check for new messages (keyed watermarks)")
wn.add_argument("key", help="Watermark key (e.g. life, peter)")
wn.add_argument("--count", "-n", type=int, default=20, help="Max messages (default: 20)")
wn.add_argument("--source", "-s", default="all", help="Source: prefix, provider name, or all")
_add_common_args(wn)
wn.set_defaults(func=_cmd_whatsnew)
```

```python
# Handler:
async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    refs = _new_ref_table()
    result = await commands.whatsnew(
        key=args.key,
        source=getattr(args, "source", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        filter=getattr(args, "filter", False),
        ref_table=refs,
    )
    if result.error:
        print(result.error)
        return
    refs.save(_refs_path())
    print(result.output)
```

Add MCP tool in `src/ts4k/server.py`:

```python
@mcp.tool()
async def whatsnew(
    key: str,
    source: str = "all",
    count: int = 20,
    fmt: str = "pipe",
    filter: bool = False,
) -> str:
    """Check for new messages using keyed watermarks.

    Each key (e.g. "life", "peter") tracks independent read positions
    per source. First run checks last 7 days. Subsequent runs pick up
    where the previous call left off.

    Args:
        key: Watermark key name (e.g. "life", "peter").
        source: Source prefix (e.g. "g"), provider name ("gmail"), or "all".
        count: Maximum messages to return (default 20).
        fmt: Output format — "pipe" (default, most compact), "json", or "xml".
        filter: Apply configured skip filters (default off).
    """
    result = await commands.whatsnew(
        key=key, source=source, count=count, fmt=fmt,
        filter=filter, ref_table=_refs,
    )
    if result.error:
        return result.error
    return result.output
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_whatsnew.py -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `uv run pytest tests/ -x -q`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/ts4k/commands.py src/ts4k/cli.py src/ts4k/server.py tests/test_whatsnew.py
git commit -m "Add whatsnew command with keyed watermarks"
```

---

### Task 6: Update status display

**Files:**
- Modify: `src/ts4k/commands.py` (get_status function, ~line 575)

**Step 1: Write the failing test**

Add to `tests/test_commands.py`:

```python
class TestStatusWhatsnewKeys:
    def test_shows_keys_in_status(self, tmp_path, monkeypatch):
        from ts4k.state import sources, stats, cache, contacts, filters
        from ts4k.state import keyed_watermarks as kwm

        for mod in (sources, stats, cache, contacts, filters, kwm):
            monkeypatch.setattr(mod, "_CONFIG_DIR", tmp_path)

        monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr(stats, "_STATS_FILE", tmp_path / "stats.json")
        monkeypatch.setattr(contacts, "_CONTACTS_FILE", tmp_path / "contacts.json")
        monkeypatch.setattr(filters, "_CONFIG_DIR", tmp_path)

        (tmp_path / "sources.json").write_text('{"g": {"provider": "gmail", "email": "t@t.com"}}')

        kwm.update("life", {"g": "2026-03-08T12:00:00Z", "o": "2026-03-07T00:00:00Z"})
        kwm.update("peter", {"g": "2026-03-05T00:00:00Z"})

        output = commands.get_status()
        assert "Whatsnew keys:" in output
        assert "life" in output
        assert "peter" in output
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_commands.py::TestStatusWhatsnewKeys -v`
Expected: FAIL — "Whatsnew keys:" not in output

**Step 3: Update get_status**

In the `get_status()` function in `src/ts4k/commands.py`, replace the per-source `wm:` display in the Sources section with a Whatsnew keys section. Remove `wm = watermarks.all()` and the `wm_str` logic. Add after the Sources section:

```python
    # Whatsnew keys
    from ts4k.state import keyed_watermarks
    keys = keyed_watermarks.list_keys()
    if keys:
        lines.append("")
        lines.append("Whatsnew keys:")
        for key in keys:
            wm_data = keyed_watermarks.get_all(key)
            num_sources = len(wm_data)
            last_run = max(wm_data.values()) if wm_data else "never"
            lines.append(f"  {key}: {num_sources} sources, last run {last_run}")
```

Remove `wm = watermarks.all()` and the `wm_str` lines from the Sources loop.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_commands.py::TestStatusWhatsnewKeys -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `uv run pytest tests/ -x -q`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/ts4k/commands.py tests/test_commands.py
git commit -m "Show whatsnew keys in status, remove per-source watermarks"
```

---

### Task 7: Update help text and clean up old watermark references

**Files:**
- Modify: `src/ts4k/cli.py` (help command output)
- Modify: `src/ts4k/commands.py` (remove old `_since_to_gmail_query`, `_since_to_iso`, old watermark imports)
- Modify: `tests/` (any tests referencing old watermark behavior in whatsnew)

**Step 1: Update help command output**

In `_cmd_help` in `cli.py`, update the commands listing:

```python
print("  updates [--since 2d] [--source S] [-n N]   Fetch messages by time range  [u]")
print("  whatsnew KEY [--source S] [-n N]            Check new (keyed watermarks)  [wn]")
```

**Step 2: Remove dead code**

- Remove `_since_to_gmail_query` and `_since_to_iso` from `commands.py` if no longer referenced
- Remove `from ts4k.state import watermarks` import if no longer used in `commands.py`
- Keep `watermarks.py` module intact (other code may still reference it, and old file stays on disk)

**Step 3: Verify list command still works**

The `list` command in `commands.py` also uses `_since_to_gmail_query` and `_since_to_iso`. Check if those functions are still needed there — if so, keep them but remove the watermark fallback (they should only accept explicit values, not read watermarks).

Run: `uv run pytest tests/ -x -q`
Expected: All PASS

**Step 4: Commit**

```bash
git add src/ts4k/commands.py src/ts4k/cli.py tests/
git commit -m "Update help text, clean up old watermark references"
```

---

### Task 8: Integration test — end to end

**Files:**
- Create: `tests/test_integration_whatsnew.py`

**Step 1: Write integration test**

```python
"""Integration test: whatsnew + updates end-to-end with mock adapters."""

import json

import pytest

from ts4k import commands
from ts4k.commands import CommandResult
from ts4k.state import keyed_watermarks as kwm


@pytest.fixture(autouse=True)
def mock_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))

    from ts4k.state import sources, stats, cache

    monkeypatch.setattr(sources, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources, "_SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(stats, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(stats, "_STATS_FILE", tmp_path / "stats.json")
    monkeypatch.setattr(cache, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(kwm, "_CONFIG_DIR", tmp_path)

    cfg = {
        "g": {"provider": "gmail", "email": "t@t.com"},
        "o": {"provider": "o365", "client_id": "fake", "tenant_id": "common"},
    }
    (tmp_path / "sources.json").write_text(json.dumps(cfg))
    return tmp_path


def _fake_messages(prefix, count, base_hour=10):
    return [
        {
            "id": f"{prefix}:msg{i}",
            "source": prefix,
            "thread_id": f"{prefix}:t{i}",
            "from": f"s{i}@test.com",
            "subject": f"Subj {i}",
            "date": f"2026-03-08T{base_hour + i:02d}:00:00Z",
            "body": f"Body {i}",
        }
        for i in range(count)
    ]


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_whatsnew_drains_messages(self, monkeypatch):
        """Multiple whatsnew calls drain all messages via advancing watermarks."""
        call_count = [0]

        async def fake_fetch(prefix, cfg, since, count):
            call_count[0] += 1
            if call_count[0] <= 2:  # first two calls have messages
                return _fake_messages(prefix, 5, base_hour=10 + call_count[0] * 5)
            return []  # drained

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        r1 = await commands.whatsnew(key="test", count=20)
        assert r1.messages_processed > 0

        # Watermarks should have advanced
        assert kwm.get("test", "g") is not None

    @pytest.mark.asyncio
    async def test_updates_is_stateless(self, monkeypatch):
        """updates does not create watermark files."""
        async def fake_fetch(prefix, cfg, since, count):
            return _fake_messages(prefix, 3)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        await commands.updates(since="1d", count=20)
        assert kwm.list_keys() == []

    @pytest.mark.asyncio
    async def test_has_more_indicator(self, monkeypatch):
        """Truncated results include has_more and remaining."""
        async def fake_fetch(prefix, cfg, since, count):
            return _fake_messages(prefix, 15)

        monkeypatch.setattr(commands, "_fetch_for_source", fake_fetch)

        result = await commands.updates(since="1d", count=5)
        assert result.has_more is True
        assert result.remaining > 0
        assert "more messages available" in result.output
```

**Step 2: Run integration tests**

Run: `uv run pytest tests/test_integration_whatsnew.py -v`
Expected: All PASS

**Step 3: Run full suite**

Run: `uv run pytest tests/ -x -q`
Expected: All PASS

**Step 4: Commit**

```bash
git add tests/test_integration_whatsnew.py
git commit -m "Add integration tests for whatsnew/updates split"
```

---

### Task 9: Drop `#` prefix from refs, per-key accumulating refs for CLI

**Context:** `#7` wastes a token vs `7`. Refs should be bare numbers everywhere — listings, get, thread, MCP. Additionally, `whatsnew` needs per-key ref files that accumulate across pagination calls. `get`/`thread` get a `--key`/`-k` flag to specify which ref file to resolve from.

**Changes:**
- **Format layer**: `#N` → `N` in listing headers and rows (`core/format.py`)
- **RefTable**: accept both `#3` and `3` in `resolve()` for backwards compat, but only emit bare numbers
- **CLI `whatsnew <key>`**: per-key ref file `refs-<key>.json`, loads existing, appends new, saves back. Cross-service: `7` can be `g:abc123`, `9` can be `w:456def`. FCFS, grows infinitely.
- **CLI `updates`**: fresh `refs.json`, overwrite per call (stateless)
- **CLI `get`/`thread`**: add `--key`/`-k` flag. With key → resolve from `refs-<key>.json`. Without → resolve from global `refs.json`.
- **MCP**: session-scoped in-memory RefTable, bare numbers, accumulates naturally

**Files:**
- Modify: `src/ts4k/core/format.py:243-284` (remove `#` from ref output)
- Modify: `src/ts4k/state/refs.py:17,48-53` (accept bare numbers in resolve)
- Modify: `src/ts4k/cli.py:45-62` (ref table helpers, `--key` on get/thread parsers)
- Modify: `src/ts4k/cli.py` (`_cmd_whatsnew`, `_cmd_get`, `_cmd_thread` handlers)
- Modify: `src/ts4k/server.py` (update docstrings from `#N` to `N`)
- Create: `tests/test_keyed_refs.py`

**Step 1: Write the failing tests**

```python
"""Tests for bare-number refs and per-key accumulating refs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ts4k.state.refs import RefTable


@pytest.fixture
def refs_dir(tmp_path):
    return tmp_path


class TestBareNumberRefs:
    def test_resolve_bare_number(self):
        """RefTable resolves bare '3' same as '#3'."""
        rt = RefTable()
        rt.assign([{"id": "g:aaa"}, {"id": "o:bbb"}, {"id": "w:ccc"}])
        assert rt.resolve("1") == "g:aaa"
        assert rt.resolve("2") == "o:bbb"
        assert rt.resolve("3") == "w:ccc"
        # Backwards compat
        assert rt.resolve("#3") == "w:ccc"


class TestKeyedRefs:
    def test_accumulates_across_saves(self, refs_dir):
        """Per-key refs grow across multiple save/load cycles."""
        path = refs_dir / "refs-life.json"

        # Page 1: assign 1, 2, 3
        rt = RefTable()
        rt.assign([{"id": "g:aaa"}, {"id": "o:bbb"}, {"id": "w:ccc"}])
        rt.save(path)

        # Page 2: load existing, assign 4, 5
        rt2 = RefTable()
        rt2.load(path)
        rt2.assign([{"id": "g:ddd"}, {"id": "o:eee"}])
        rt2.save(path)

        # Verify all 5 refs exist
        rt3 = RefTable()
        rt3.load(path)
        assert rt3.resolve("1") == "g:aaa"
        assert rt3.resolve("2") == "o:bbb"
        assert rt3.resolve("3") == "w:ccc"
        assert rt3.resolve("4") == "g:ddd"
        assert rt3.resolve("5") == "o:eee"

    def test_cross_service_ids(self, refs_dir):
        """Refs span services — 1 gmail, 2 whatsapp, 3 o365."""
        rt = RefTable()
        rt.assign([
            {"id": "g:gmail123"},
            {"id": "w:whatsapp456"},
            {"id": "o:o365789"},
        ])
        assert rt.resolve("1") == "g:gmail123"
        assert rt.resolve("2") == "w:whatsapp456"
        assert rt.resolve("3") == "o:o365789"

    def test_dedup_existing_ids(self, refs_dir):
        """Same message ID across pages doesn't get a new ref number."""
        path = refs_dir / "refs-life.json"

        rt = RefTable()
        rt.assign([{"id": "g:aaa"}, {"id": "o:bbb"}])
        rt.save(path)

        rt2 = RefTable()
        rt2.load(path)
        result = rt2.assign([{"id": "g:aaa"}, {"id": "g:ccc"}])
        assert result["g:aaa"] == 1
        assert result["g:ccc"] == 3


class TestKeyedRefLookup:
    def test_key_flag_resolves_from_key_file(self, refs_dir):
        """--key life resolves from refs-life.json."""
        rt = RefTable()
        rt.assign([{"id": "g:keyed_msg"}])
        rt.save(refs_dir / "refs-life.json")

        rt_load = RefTable()
        rt_load.load(refs_dir / "refs-life.json")
        assert rt_load.resolve("1") == "g:keyed_msg"

    def test_no_key_resolves_from_global(self, refs_dir):
        """Without --key, resolves from refs.json."""
        rt = RefTable()
        rt.assign([{"id": "o:global_msg"}])
        rt.save(refs_dir / "refs.json")

        rt_load = RefTable()
        rt_load.load(refs_dir / "refs.json")
        assert rt_load.resolve("1") == "o:global_msg"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_keyed_refs.py -v`
Expected: FAIL — `rt.resolve("1")` returns None (only `#1` works currently)

**Step 3: Update RefTable to accept bare numbers**

In `src/ts4k/state/refs.py`, change the resolve pattern and method:

```python
_REF_PATTERN = re.compile(r"^#?(\d+)$")
```

**Step 4: Remove `#` from format output**

In `src/ts4k/core/format.py`, change `_listing_pipe_refs`:

- Header: `"#|SOURCE|FROM|..."` → `"N|SOURCE|FROM|..."`  (or just remove # from column name)
- Rows: `f"#{ref}|..."` → `f"{ref}|..."`

**Step 5: Update CLI**

Add `_refs_path` key parameter, `_load_ref_table_for_key`, `--key`/`-k` on get/thread parsers:

```python
def _refs_path(key: str | None = None) -> Path:
    base = state.get_config_dir().path
    if key:
        return base / f"refs-{key}.json"
    return base / "refs.json"
```

Update `_cmd_whatsnew` to load and accumulate per-key refs:

```python
async def _cmd_whatsnew(args: argparse.Namespace) -> None:
    refs = RefTable()
    refs.load(_refs_path(args.key))  # load existing, accumulate
    result = await commands.whatsnew(
        key=args.key,
        source=getattr(args, "source", None),
        count=getattr(args, "count", 20) or 20,
        fmt=getattr(args, "format", "pipe") or "pipe",
        filter=getattr(args, "filter", False),
        ref_table=refs,
    )
    if result.error:
        print(result.error)
        return
    refs.save(_refs_path(args.key))  # save accumulated
    print(result.output)
```

Update `_cmd_get` and `_cmd_thread`:

```python
async def _cmd_get(args: argparse.Namespace) -> None:
    msg_id = args.id
    if msg_id.isdigit():
        key = getattr(args, "key", None)
        rt = RefTable()
        rt.load(_refs_path(key))
        resolved = rt.resolve(msg_id)
        if resolved is None:
            label = f"key '{key}'" if key else "global refs"
            print(f"Ref {msg_id} not found in {label}. Run 'whatsnew' or 'updates' first.")
            sys.exit(1)
        msg_id = resolved
    result = await commands.get_message(
        id=msg_id,
        fmt=getattr(args, "format", "pipe") or "pipe",
    )
    if result.error:
        print(result.error)
        sys.exit(1)
    print(result.output)
```

Add `--key`/`-k` to get and thread parsers:

```python
get.add_argument("--key", "-k", help="Whatsnew key for ref lookup (e.g. life)")
th.add_argument("--key", "-k", help="Whatsnew key for ref lookup (e.g. life)")
```

**Step 6: Update MCP server docstrings**

Change all `(#1, #2, ...)` references to `(1, 2, ...)` and `"#3"` to `"3"` in `server.py`.

**Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/ -x -q`
Expected: All PASS (some existing tests may need `#` removed from assertions)

**Step 8: Commit**

```bash
git add src/ts4k/state/refs.py src/ts4k/core/format.py src/ts4k/cli.py src/ts4k/server.py tests/
git commit -m "Bare number refs, per-key accumulating refs, --key flag on get/thread"
```

---

### Task 10: Update help, skill, and LLM-facing text

**Context:** All user/agent-facing text must clearly explain the new command split, bare number refs, and `--key` flag. This includes `ts4k help`, `ts4k help --llm`, `ts4k skill`, MCP tool docstrings, and the CLI `--help` descriptions.

**Files:**
- Modify: `src/ts4k/cli.py` (`_cmd_help` output, parser descriptions)
- Modify: `src/ts4k/commands.py` (`llm_help()` if it exists)
- Modify: `src/ts4k/server.py` (MCP tool docstrings)
- Modify: skill file if applicable

**Step 1: Update `ts4k help` quick reference**

```python
print("  updates [--since 2d] [--source S] [-n N]   Fetch messages by time range  [u]")
print("  whatsnew KEY [--source S] [-n N]            Check new (keyed watermarks)  [wn]")
print("  list [-q QUERY] [--source S] [-n N]         Search messages              [l]")
print("  get [-k KEY] ID                             Read a message               [g]")
print("  thread [-k KEY] TID                         Read a thread/chat           [t]")
```

Update the Flags/IDs section:

```python
print("Refs:  listings assign numbers (1, 2, 3...) — use with get/thread")
print("       whatsnew refs accumulate per key; use get -k KEY N to resolve")
print("IDs:   g:xxx (Gmail), o:xxx (O365), w:xxx (WhatsApp)")
```

**Step 2: Update `ts4k help --llm`**

Ensure the LLM-oriented help explains:
- `updates` is stateless, `whatsnew KEY` tracks watermarks per key
- Refs are bare numbers, cross-service
- Use `get -k KEY N` to read a message from a whatsnew context
- `has_more` / remaining count in truncated output signals more pages available

**Step 3: Update MCP tool docstrings**

Already covered in Tasks 4-5 and 9, but verify consistency:
- `updates` tool: mentions stateless, no watermarks
- `whatsnew` tool: mentions keyed watermarks, pagination, bare number refs
- `get`/`thread` tools: mention bare number refs

**Step 4: Update skill text**

Check the skill command output (`ts4k skill`) and update if it references `#N` or the old `whatsnew`/`updates` merged command.

**Step 5: Run full test suite**

Run: `uv run pytest tests/ -x -q`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/ts4k/cli.py src/ts4k/commands.py src/ts4k/server.py
git commit -m "Update help, skill, and LLM text for whatsnew/updates split"
```

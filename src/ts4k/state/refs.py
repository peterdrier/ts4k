"""Short reference table — maps ``#N`` → full message ID.

Saves ~92% tokens on message IDs in pipe listings.  ``#1`` costs 2 tokens
vs 136 for a raw O365 ID.

Two modes:
- **MCP (accumulate)**: refs persist across tool calls within one connection.
- **CLI (last-listing-wins)**: each listing replaces the ref file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REF_PATTERN = re.compile(r"^#(\d+)$")


class RefTable:
    """Short reference table mapping ``#N`` → full message ID."""

    def __init__(self) -> None:
        self._refs: dict[int, str] = {}  # ref_num → full_id
        self._reverse: dict[str, int] = {}  # full_id → ref_num
        self._next: int = 1

    def assign(self, messages: list[dict]) -> dict[str, int]:
        """Assign refs to messages.  Returns ``{full_id: ref_num}``.

        Reuses existing ref if message already has one (MCP accumulate mode).
        """
        result: dict[str, int] = {}
        for msg in messages:
            full_id = msg.get("id", "")
            if not full_id:
                continue
            if full_id in self._reverse:
                result[full_id] = self._reverse[full_id]
            else:
                num = self._next
                self._refs[num] = full_id
                self._reverse[full_id] = num
                self._next = num + 1
                result[full_id] = num
        return result

    def resolve(self, ref: str) -> str | None:
        """Resolve ``'#3'`` → full_id.  Returns ``None`` if not found."""
        m = _REF_PATTERN.match(ref.strip())
        if not m:
            return None
        num = int(m.group(1))
        return self._refs.get(num)

    def clear(self) -> None:
        """Reset table (CLI per-command mode)."""
        self._refs.clear()
        self._reverse.clear()
        self._next = 1

    # ------------------------------------------------------------------
    # Persistence (CLI mode)
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write ref table to JSON file."""
        from ts4k.state._io import safe_write_json

        data = {
            "refs": {str(k): v for k, v in self._refs.items()},
            "next": self._next,
        }
        safe_write_json(path, data)

    def load(self, path: Path) -> None:
        """Load ref table from JSON file.  Silently resets on missing/corrupt."""
        self.clear()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            refs = raw.get("refs", {})
            for k, v in refs.items():
                num = int(k)
                self._refs[num] = v
                self._reverse[v] = num
            self._next = raw.get("next", max(self._refs, default=0) + 1)
        except (json.JSONDecodeError, OSError, ValueError):
            self.clear()

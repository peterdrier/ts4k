"""Base adapter interface for ts4k platform adapters.

Every platform adapter (Gmail, WhatsApp, Telegram, ...) implements this
interface so the core pipeline can treat them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseAdapter(ABC):
    """Interface that all platform adapters must implement.

    Adapters wrap platform APIs (direct or via MCP bridges).  Each adapter
    translates between the upstream tool's native response format and the
    normalised dicts that the
    ts4k pipeline (normalize -> filter -> format) expects.

    The *source_prefix* is set at construction time — it's the user-chosen
    prefix from the source config (e.g. ``'g'``, ``'gn'``, ``'w'``).
    """

    @property
    @abstractmethod
    def source_prefix(self) -> str:
        """Short tag used to namespace IDs (e.g. ``'g'``, ``'gn'``, ``'w'``).

        All IDs returned by this adapter are prefixed with
        ``<source_prefix>:`` so they remain unique across sources.
        """

    @abstractmethod
    async def connect(self) -> None:
        """Establish the upstream connection (MCP session, CLI check, etc.).

        Must be called before any data methods.  Adapters that use a
        context-manager transport (like ``stdio_client``) should store the
        resulting session for reuse.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the upstream connection gracefully."""

    @abstractmethod
    async def whatsnew(self, since: str | None = None) -> list[dict]:
        """Return new activity since *since* (ISO-8601 timestamp).

        If *since* is ``None``, the adapter should fall back to its own
        stored watermark or a sensible default (e.g. last 24 h).

        Each item in the returned list is a normalised header dict with at
        least: ``id``, ``thread_id``, ``from``, ``subject``, ``date``.
        """

    @abstractmethod
    async def list_messages(
        self,
        query: str | None = None,
        count: int = 20,
        page_token: str | None = None,
    ) -> list[dict]:
        """Return message-header dicts matching *query*.

        The dicts must contain at least: ``id``, ``thread_id``, ``from``,
        ``subject``, ``date``.

        When *page_token* is provided, fetch the next page of results
        starting from that token.  If more pages exist, the **last** entry
        in the returned list will carry a ``_next_page_token`` key.
        """

    @abstractmethod
    async def read_message(self, msg_id: str) -> dict:
        """Return the full content of a single message.

        The returned dict must contain at least: ``id``, ``from``,
        ``subject``, ``date``, ``body``.  Optional keys: ``to``, ``cc``,
        ``message_id`` (RFC-822), ``attachments``.
        """

    @abstractmethod
    async def read_thread(self, thread_id: str) -> dict:
        """Return the full content of a thread.

        The returned dict must contain at least: ``thread_id``,
        ``subject``, ``message_count``, ``messages`` (list of message
        dicts as returned by :meth:`read_message`).
        """

    async def mailbox_stats(self) -> dict | None:
        """Return live mailbox label/folder counts, or None if unsupported.

        Only email adapters need to override this.  Returns a dict like::

            {"provider": "gmail", "labels": [
                {"name": "Inbox", "total": 142, "unread": 23}, ...
            ]}
        """
        return None

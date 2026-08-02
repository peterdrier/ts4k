"""Base adapter interface for ts4k platform adapters.

Every platform adapter (Gmail, WhatsApp, Telegram, ...) implements this
interface so the core pipeline can treat them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ts4k.core.levels import AccessLevel


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
    def access_level(self) -> AccessLevel:
        """Permission level for this source connection.

        Defaults to READONLY. Concrete adapters set this via config.
        """
        return getattr(self, "_access_level", AccessLevel.READONLY)

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
    async def whatsnew(
        self,
        since: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
        count: int = 200,
    ) -> list[dict]:
        """Return new activity since *since* (ISO-8601 timestamp).

        If *since* is ``None``, the adapter should fall back to its own
        stored watermark or a sensible default (e.g. last 24 h).

        *sender* filters to an exact email address; *domain* filters to
        all addresses at that domain.  Both are optional and adapter-
        specific (some adapters may ignore them).

        *count* is the fetch target — adapters that paginate should keep
        fetching pages until they have *count* results or run out. Best
        effort; adapters with hard platform caps may return fewer.

        Each item in the returned list is a normalised header dict with at
        least: ``id``, ``thread_id``, ``from``, ``subject``, ``date``.
        """

    @abstractmethod
    async def list_messages(
        self,
        query: str | None = None,
        count: int = 20,
        page_token: str | None = None,
        sender: str | None = None,
        domain: str | None = None,
    ) -> list[dict]:
        """Return message-header dicts matching *query*.

        The dicts must contain at least: ``id``, ``thread_id``, ``from``,
        ``subject``, ``date``.

        *sender* filters to an exact email address; *domain* filters to
        all addresses at that domain.  Both are optional and adapter-
        specific (some adapters may ignore them).

        When *page_token* is provided, fetch the next page of results
        starting from that token.  If more pages exist, the **last** entry
        in the returned list will carry a ``_next_page_token`` key.
        """

    @abstractmethod
    async def read_message(self, msg_id: str, prefer_html: bool = False) -> dict:
        """Return the full content of a single message.

        The returned dict must contain at least: ``id``, ``from``,
        ``subject``, ``date``, ``body``.  Optional keys: ``to``, ``cc``,
        ``message_id`` (RFC-822), ``attachments``.

        *prefer_html* requests the HTML body part when the platform offers
        a choice (e.g. Gmail multipart/alternative), so readable-mode
        normalization has markup to preserve. Adapters that don't offer a
        choice (already HTML-only, or no HTML at all) may ignore it.
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

    # -- Management methods (optional, require level >= MODIFY) ---------------
    # ts4k NEVER sends messages. There is no send method. This is by design.

    async def archive_message(self, msg_id: str) -> dict:
        """Archive a message (remove from inbox, keep in mailbox)."""
        raise NotImplementedError(f"{type(self).__name__} does not support archive")

    async def unarchive_message(self, msg_id: str) -> dict:
        """Unarchive a message (return to inbox)."""
        raise NotImplementedError(f"{type(self).__name__} does not support unarchive")

    async def label_message(self, msg_id: str, label: str) -> dict:
        """Add a label/category to a message."""
        raise NotImplementedError(f"{type(self).__name__} does not support labeling")

    async def unlabel_message(self, msg_id: str, label: str) -> dict:
        """Remove a label/category from a message."""
        raise NotImplementedError(f"{type(self).__name__} does not support unlabeling")

    async def mark_read(self, msg_id: str) -> dict:
        """Mark a message as read."""
        raise NotImplementedError(f"{type(self).__name__} does not support mark_read")

    async def mark_unread(self, msg_id: str) -> dict:
        """Mark a message as unread."""
        raise NotImplementedError(f"{type(self).__name__} does not support mark_unread")

    async def trash_message(self, msg_id: str) -> dict:
        """Move a message to trash (recoverable)."""
        raise NotImplementedError(f"{type(self).__name__} does not support trash")

    async def list_labels(self) -> list[dict]:
        """List available labels/categories/folders."""
        raise NotImplementedError(f"{type(self).__name__} does not support list_labels")

    async def create_label(self, name: str) -> dict:
        """Create a new label/category/folder."""
        raise NotImplementedError(f"{type(self).__name__} does not support create_label")

    # -- Draft methods (optional, require level >= DRAFT) ---------------------

    async def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to_message_id: str | None = None,
    ) -> dict:
        """Create a draft message. Does NOT send.

        When reply_to_message_id is provided, the draft threads as a reply
        with proper headers and blockquoted original.

        Returns: {"id": "<prefixed draft ID>", "status": "draft_created"}
        """
        raise NotImplementedError(f"{type(self).__name__} does not support create_draft")

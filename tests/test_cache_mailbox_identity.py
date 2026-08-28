"""Cached entries carry a stable mailbox identity (ts4k#87).

Source prefixes are user-chosen and reassignable: a prefix that pointed at
account A can later be repointed at account B.  Entries record the mailbox
they came from, and read APIs invalidate on mismatch, so the old account's
cached messages are never misattributed to the new one.
"""

from __future__ import annotations

import json

import pytest

from ts4k import commands
from ts4k.state import cache


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import ts4k.state.sources as sources_mod

    monkeypatch.setattr(cache, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "_INDEX_FILE", tmp_path / "cache" / "index.json")
    monkeypatch.setattr(cache, "_BODIES_DIR", tmp_path / "cache" / "bodies")
    monkeypatch.setattr(sources_mod, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(sources_mod, "_SOURCES_FILE", tmp_path / "sources.json")
    yield tmp_path


HDR = {"source": "o", "from": "boss@account-a.com", "subject": "Q3",
       "date": "2026-08-01T10:00:00Z"}


class TestCacheMailboxGate:
    def test_entry_records_mailbox(self):
        cache.store_header("o:1", HDR, provider="o365", mailbox="a@corp.com")
        entry = cache.get_header("o:1")
        assert entry["_mailbox"] == "a@corp.com"

    def test_matching_mailbox_hits(self):
        cache.store_message(
            "o:1", {**HDR, "body": "text"}, provider="o365", mailbox="a@corp.com"
        )
        assert cache.get_message("o:1", mailbox="a@corp.com") is not None
        assert cache.has("o:1", mailbox="a@corp.com")

    def test_mismatched_mailbox_misses(self):
        cache.store_message(
            "o:1", {**HDR, "body": "text"}, provider="o365", mailbox="a@corp.com"
        )
        assert cache.get_message("o:1", mailbox="b@corp.com") is None
        assert cache.get_header("o:1", mailbox="b@corp.com") is None
        assert not cache.has("o:1", mailbox="b@corp.com")

    def test_no_expectation_skips_check(self):
        cache.store_header("o:1", HDR, provider="o365", mailbox="a@corp.com")
        assert cache.get_header("o:1") is not None

    def test_list_headers_drops_mismatched_prefix_entries(self):
        cache.store_header("o:1", HDR, provider="o365", mailbox="a@corp.com")
        cache.store_header(
            "g:1", {**HDR, "source": "g"}, provider="gmail", mailbox="gmail:me@gmail.com"
        )

        # "o" now points at account B; "g" unchanged; unmapped prefixes pass.
        rows = cache.list_headers(
            mailboxes={"o": "b@corp.com", "g": "gmail:me@gmail.com"}
        )
        assert [r["id"] for r in rows] == ["g:1"]

    def test_batch_store_records_mailbox(self):
        with cache.CacheBatch() as cb:
            cb.store_header("o:1", HDR, provider="o365", mailbox="a@corp.com")
        assert cache.get_header("o:1", mailbox="a@corp.com") is not None
        assert cache.get_header("o:1", mailbox="b@corp.com") is None

    def test_pre_identity_schema_entries_are_stale(self):
        """v2 entries carry no mailbox to validate — discarded via the
        SCHEMA_VERSION bump rather than misread."""
        cache.store_header("o:1", HDR, provider="o365", mailbox="a@corp.com")
        index = json.loads(cache._INDEX_FILE.read_text(encoding="utf-8"))
        index["messages"]["o:old"] = {
            **HDR, "_schema_version": 2, "_cached_at": "2026-01-01T00:00:00Z",
        }
        cache._INDEX_FILE.write_text(json.dumps(index), encoding="utf-8")

        assert cache.get_header("o:old") is None
        assert cache.get_header("o:1") is not None


class TestPrefixReassignment:
    """Acceptance: repointing a prefix across two accounts must not surface
    the old account's cached messages (ts4k#87)."""

    def _configure(self, mailbox):
        from ts4k.state import sources
        cfg = {"o": {"provider": "o365", "client_id": "cid", "mailbox": mailbox}}
        sources._SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        sources._SOURCES_FILE.write_text(json.dumps(cfg))
        return cfg["o"]

    @pytest.mark.asyncio
    async def test_get_message_ignores_old_accounts_cache(self, monkeypatch):
        cfg_a = self._configure("a@corp.com")
        cache.store_message(
            "o:1", {**HDR, "body": "account A body"},
            provider="o365", mailbox=commands._mailbox_identity(cfg_a),
        )

        # Served from cache while the prefix still points at account A.
        result = await commands.get_message("o:1")
        assert "account A body" in result.output

        # Repoint "o" at account B: the cache entry must be a miss, so the
        # command reaches the adapter instead of serving A's message.
        self._configure("b@corp.com")

        class _Sentinel:
            async def __aenter__(self):
                raise RuntimeError("adapter fetch attempted")

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(commands, "_make_adapter", lambda p, c: _Sentinel())
        with pytest.raises(RuntimeError, match="adapter fetch attempted"):
            await commands.get_message("o:1")

    def test_overview_activity_excludes_old_account(self):
        cfg_a = self._configure("a@corp.com")
        cache.store_header(
            "o:1", HDR, provider="o365",
            mailbox=commands._mailbox_identity(cfg_a),
        )
        assert commands.source_activity("o", provider="o365")["count"] == 1

        self._configure("b@corp.com")
        assert commands.source_activity("o", provider="o365")["count"] == 0

    def test_mailbox_identity_fallbacks(self):
        assert commands._mailbox_identity(
            {"provider": "gmail", "email": "me@gmail.com"}
        ) == "gmail:me@gmail.com"
        assert commands._mailbox_identity(
            {"provider": "o365", "client_id": "cid", "mailbox": "mb@corp.com"}
        ) == "o365:mb@corp.com"
        # /me sources: the authenticated username recorded at source-add
        # time identifies the account, so re-authing under the same app
        # registration still invalidates the cache.
        assert commands._mailbox_identity(
            {"provider": "o365", "client_id": "cid", "tenant_id": "t1",
             "email": "signed-in@corp.com"}
        ) == "o365:signed-in@corp.com"
        # Last resort for /me sources added before email was recorded.
        assert commands._mailbox_identity(
            {"provider": "o365", "client_id": "cid", "tenant_id": "t1"}
        ) == "o365:cid/t1"

    def test_identity_distinguishes_providers_sharing_an_address(self):
        # A prefix repointed between providers during a mail migration can
        # keep the same address — the identity must still change so the old
        # provider's cached entries are invalidated.
        gmail = commands._mailbox_identity(
            {"provider": "gmail", "email": "me@corp.com"}
        )
        o365 = commands._mailbox_identity(
            {"provider": "o365", "client_id": "cid", "mailbox": "me@corp.com"}
        )
        assert gmail != o365


class TestBodyInvalidationOnRestamp:
    """Bodies live in separate untagged files — restamping a header with a
    new mailbox must not leave the old account's body to be served under
    the new header."""

    def test_header_restamp_drops_other_accounts_body(self):
        cache.store_message(
            "o:1", {**HDR, "body": "account A body"},
            provider="o365", mailbox="a@corp.com",
        )
        # Header-only refresh from the repointed account (listing/preload).
        cache.store_header("o:1", HDR, provider="o365", mailbox="b@corp.com")

        msg = cache.get_message("o:1", mailbox="b@corp.com")
        assert msg is not None
        assert "body" not in msg
        assert cache.get_body("o:1") is None

    def test_same_account_restamp_keeps_body(self):
        cache.store_message(
            "o:1", {**HDR, "body": "the body"},
            provider="o365", mailbox="a@corp.com",
        )
        cache.store_header("o:1", HDR, provider="o365", mailbox="a@corp.com")
        msg = cache.get_message("o:1", mailbox="a@corp.com")
        assert msg is not None and msg["body"] == "the body"

    def test_batch_restamp_drops_other_accounts_body(self):
        cache.store_message(
            "o:1", {**HDR, "body": "account A body"},
            provider="o365", mailbox="a@corp.com",
        )
        with cache.CacheBatch() as cb:
            cb.store_header("o:1", HDR, provider="o365", mailbox="b@corp.com")
        assert cache.get_body("o:1") is None


class TestAuthRefreshesRecordedIdentity:
    """`ts4k auth <prefix>` on a /me source must persist the account that
    actually authenticated, so a re-auth as another user invalidates the
    cache (cache identity keys off cfg['email'])."""

    def test_auth_updates_email_from_token_claims(self, monkeypatch):
        # The account comes from the token result that just authenticated —
        # not from a scan of the (possibly multi-account) MSAL cache.
        from ts4k import cli
        from ts4k.state import sources

        sources.add(
            "o", provider="o365", client_id="cid", tenant_id="t1",
            email="old@corp.com",
        )
        monkeypatch.setattr(
            "ts4k.auth.microsoft.get_credentials",
            lambda *a, **kw: {
                "access_token": "tok",
                "id_token_claims": {"preferred_username": "new@corp.com"},
            },
        )
        monkeypatch.setattr(
            "ts4k.commands._resolve_o365_username",
            lambda cfg: "stale-first-cache-entry@corp.com",
        )

        cli._auth_o365("o", sources.get("o"), no_calendar=True)

        assert sources.get("o")["email"] == "new@corp.com"

    def test_auth_falls_back_to_cache_scan_without_claims(self, monkeypatch):
        from ts4k import cli
        from ts4k.state import sources

        sources.add("o", provider="o365", client_id="cid", tenant_id="t1")
        monkeypatch.setattr(
            "ts4k.auth.microsoft.get_credentials",
            lambda *a, **kw: {"access_token": "tok"},
        )
        monkeypatch.setattr(
            "ts4k.commands._resolve_o365_username", lambda cfg: "me@corp.com"
        )

        cli._auth_o365("o", sources.get("o"), no_calendar=True)

        assert sources.get("o")["email"] == "me@corp.com"

    def test_auth_leaves_explicit_mailbox_sources_alone(self, monkeypatch):
        from ts4k import cli
        from ts4k.state import sources

        sources.add(
            "o", provider="o365", client_id="cid", mailbox="shared@corp.com",
        )
        monkeypatch.setattr(
            "ts4k.auth.microsoft.get_credentials", lambda *a, **kw: object()
        )

        cli._auth_o365("o", sources.get("o"), no_calendar=True)

        assert "email" not in sources.get("o")


class TestGmailAdapterCacheGate:
    """The Gmail adapter's internal cache lookup must validate the account:
    an unchecked hit would return the old account's header, which the
    listing path then restamps with the new identity."""

    @pytest.mark.asyncio
    async def test_list_messages_rejects_other_accounts_cache_hit(self):
        from unittest.mock import MagicMock

        from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig

        cache.store_header(
            "g:msg1",
            {"source": "g", "from": "old@sender.com", "subject": "old account",
             "date": "2026-08-01T10:00:00Z"},
            provider="gmail", mailbox="gmail:old@gmail.com",
        )

        adapter = GmailAdapter(GmailAdapterConfig(user_email="new@gmail.com"))
        adapter._service = MagicMock()
        adapter._service.users.return_value.messages.return_value.list.return_value.execute = MagicMock(
            return_value={"messages": [{"id": "msg1"}]}
        )

        fetched: list[str] = []

        async def _record_fetch(service, msg_ids):
            fetched.extend(msg_ids)
            return []

        adapter._chunked_batch_fetch = _record_fetch

        results = await adapter.list_messages("newer_than:1d")

        # The stale hit must not be served — the adapter re-fetches.
        assert results == []
        assert fetched == ["msg1"]

    @pytest.mark.asyncio
    async def test_list_messages_serves_own_accounts_cache_hit(self):
        from unittest.mock import MagicMock

        from ts4k.adapters.gmail import GmailAdapter, GmailAdapterConfig

        cache.store_header(
            "g:msg1",
            {"source": "g", "from": "boss@corp.com", "subject": "own account",
             "date": "2026-08-01T10:00:00Z"},
            provider="gmail", mailbox="gmail:me@gmail.com",
        )

        adapter = GmailAdapter(GmailAdapterConfig(user_email="me@gmail.com"))
        adapter._service = MagicMock()
        adapter._service.users.return_value.messages.return_value.list.return_value.execute = MagicMock(
            return_value={"messages": [{"id": "msg1"}]}
        )

        async def _no_fetch(service, msg_ids):
            raise AssertionError("batch fetch attempted on cache hit")

        adapter._chunked_batch_fetch = _no_fetch

        results = await adapter.list_messages("newer_than:1d")
        assert len(results) == 1
        assert results[0]["subject"] == "own account"

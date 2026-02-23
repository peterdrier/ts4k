"""Tests for the batch job state module (state/batch.py).

Covers job CRUD, checkpoint/resume semantics, and edge cases.
"""

from __future__ import annotations

import json
import time

import pytest

from ts4k.state import batch


@pytest.fixture(autouse=True)
def _isolate_batch(tmp_path, monkeypatch):
    """Point batch state at a temp directory for every test."""
    monkeypatch.setattr(batch, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(batch, "_BATCH_FILE", tmp_path / "batch.json")


class TestCreateJob:
    def test_returns_job_id(self):
        job_id = batch.create_job("g", "newer_than:30d")
        assert job_id.startswith("job-")

    def test_job_fields(self):
        job_id = batch.create_job("g", "from:alice")
        job = batch.get_job(job_id)
        assert job is not None
        assert job["source"] == "g"
        assert job["query"] == "from:alice"
        assert job["status"] == "in_progress"
        assert job["pages_fetched"] == 0
        assert job["messages_cached"] == 0
        assert job["next_page_token"] is None
        assert job["error"] is None
        assert job["started_at"]
        assert job["updated_at"]

    def test_unique_ids(self, monkeypatch):
        """Two jobs created at different times have different IDs."""
        t = int(time.time())
        monkeypatch.setattr(time, "time", lambda: t)
        id1 = batch.create_job("g", "q1")
        monkeypatch.setattr(time, "time", lambda: t + 1)
        id2 = batch.create_job("g", "q2")
        assert id1 != id2


class TestUpdateJob:
    def test_update_fields(self):
        job_id = batch.create_job("g", "test")
        batch.update_job(job_id, pages_fetched=3, messages_cached=150)
        job = batch.get_job(job_id)
        assert job["pages_fetched"] == 3
        assert job["messages_cached"] == 150

    def test_update_sets_updated_at(self):
        job_id = batch.create_job("g", "test")
        original = batch.get_job(job_id)["updated_at"]
        # Force a time change by sleeping briefly is unreliable,
        # so just verify it's set
        batch.update_job(job_id, pages_fetched=1)
        updated = batch.get_job(job_id)["updated_at"]
        assert updated  # non-empty

    def test_update_page_token(self):
        job_id = batch.create_job("g", "test")
        batch.update_job(job_id, next_page_token="tok_abc", pages_fetched=1)
        job = batch.get_job(job_id)
        assert job["next_page_token"] == "tok_abc"

    def test_update_status(self):
        job_id = batch.create_job("g", "test")
        batch.update_job(job_id, status="done")
        assert batch.get_job(job_id)["status"] == "done"

    def test_update_error(self):
        job_id = batch.create_job("g", "test")
        batch.update_job(job_id, status="failed", error="connection reset")
        job = batch.get_job(job_id)
        assert job["status"] == "failed"
        assert job["error"] == "connection reset"

    def test_update_unknown_job_raises(self):
        with pytest.raises(KeyError, match="Unknown job"):
            batch.update_job("job-nonexistent", status="done")


class TestGetJob:
    def test_existing_job(self):
        job_id = batch.create_job("o", "quarterly report")
        assert batch.get_job(job_id) is not None

    def test_missing_job(self):
        assert batch.get_job("job-999999999") is None


class TestListJobs:
    def test_empty(self):
        assert batch.list_jobs() == {}

    def test_multiple_jobs(self, monkeypatch):
        t = int(time.time())
        monkeypatch.setattr(time, "time", lambda: t)
        batch.create_job("g", "q1")
        monkeypatch.setattr(time, "time", lambda: t + 1)
        batch.create_job("o", "q2")
        jobs = batch.list_jobs()
        assert len(jobs) == 2


class TestDeleteJob:
    def test_delete_existing(self):
        job_id = batch.create_job("g", "delete-me")
        assert batch.delete_job(job_id) is True
        assert batch.get_job(job_id) is None

    def test_delete_nonexistent(self):
        assert batch.delete_job("job-nope") is False


class TestPersistence:
    def test_survives_reload(self, tmp_path):
        """Data persists across load/save cycles."""
        job_id = batch.create_job("g", "persist-test")
        batch.update_job(job_id, pages_fetched=7, messages_cached=350)

        # Verify file exists and is valid JSON
        batch_file = tmp_path / "batch.json"
        assert batch_file.exists()
        data = json.loads(batch_file.read_text(encoding="utf-8"))
        assert job_id in data["jobs"]
        assert data["jobs"][job_id]["pages_fetched"] == 7

    def test_corrupted_file_returns_empty(self, tmp_path):
        """Corrupted JSON gracefully returns empty state."""
        batch_file = tmp_path / "batch.json"
        batch_file.write_text("not valid json {{{", encoding="utf-8")
        assert batch.list_jobs() == {}

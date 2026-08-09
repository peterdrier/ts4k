"""Tests for ts4k.state.stats."""


import pytest

from ts4k.state import stats


@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    """Point stats at a temp directory for every test."""
    monkeypatch.setenv("TS4K_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(stats, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(stats, "_STATS_FILE", tmp_path / "stats.json")
    return tmp_path


class TestRecord:
    def test_creates_file_on_first_record(self, tmp_config_dir):
        stats.record(command="wn", source="g", bytes_in=1000, bytes_out=200, messages=5)
        assert (tmp_config_dir / "stats.json").exists()

    def test_accumulates_totals(self):
        stats.record(command="wn", source="g", bytes_in=1000, bytes_out=200, messages=5)
        stats.record(command="wn", source="w", bytes_in=500, bytes_out=100, messages=3)
        data = stats.get_all()
        assert data["total_bytes_in"] == 1500
        assert data["total_bytes_out"] == 300
        assert data["total_messages"] == 8

    def test_tracks_by_source(self):
        stats.record(command="wn", source="g", bytes_in=1000, bytes_out=200, messages=5)
        stats.record(command="l", source="g", bytes_in=500, bytes_out=100, messages=2)
        stats.record(command="wn", source="w", bytes_in=300, bytes_out=60, messages=3)
        data = stats.get_all()
        assert data["by_source"]["g"]["bytes_in"] == 1500
        assert data["by_source"]["g"]["messages"] == 7
        assert data["by_source"]["w"]["bytes_in"] == 300

    def test_tracks_by_command(self):
        stats.record(command="wn", source="g", bytes_in=1000, bytes_out=200, messages=5)
        stats.record(command="wn", source="w", bytes_in=500, bytes_out=100, messages=3)
        stats.record(command="g", source="g", bytes_in=2000, bytes_out=400, messages=1)
        data = stats.get_all()
        assert data["by_command"]["wn"]["calls"] == 2
        assert data["by_command"]["wn"]["bytes_in"] == 1500
        assert data["by_command"]["g"]["calls"] == 1


class TestSavingsPct:
    def test_zero_when_no_data(self):
        assert stats.savings_pct() == 0.0

    def test_calculates_savings(self):
        stats.record(command="wn", source="g", bytes_in=1000, bytes_out=200, messages=5)
        assert stats.savings_pct() == 80.0

    def test_zero_savings_when_equal(self):
        stats.record(command="wn", source="g", bytes_in=100, bytes_out=100, messages=1)
        assert stats.savings_pct() == 0.0

    def test_high_savings(self):
        stats.record(command="wn", source="g", bytes_in=10000, bytes_out=500, messages=10)
        assert stats.savings_pct() == 95.0


class TestGetAll:
    def test_empty_when_no_file(self):
        data = stats.get_all()
        assert data["total_bytes_in"] == 0
        assert data["total_messages"] == 0
        assert data["by_source"] == {}
        assert data["by_command"] == {}


class TestReset:
    def test_clears_all(self):
        stats.record(command="wn", source="g", bytes_in=1000, bytes_out=200, messages=5)
        stats.reset()
        assert stats.get_all()["total_bytes_in"] == 0

    def test_reset_when_empty(self):
        stats.reset()  # no error


class TestCorruptFile:
    def test_handles_invalid_json(self, tmp_config_dir):
        (tmp_config_dir / "stats.json").write_text("not json")
        assert stats.get_all()["total_bytes_in"] == 0
        # Should be able to write over corrupt file
        stats.record(command="wn", source="g", bytes_in=1000, bytes_out=200, messages=5)
        assert stats.get_all()["total_bytes_in"] == 1000

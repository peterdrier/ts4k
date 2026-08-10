"""Tests for ts4k.state.media."""

import stat
import sys

import pytest

from ts4k.state import media

# Only the mode assertions are POSIX-specific; the traversal guard matters
# on every platform, and most of all on the one with two path separators.
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits don't apply on Windows"
)


@pytest.fixture(autouse=True)
def tmp_media_dir(tmp_path, monkeypatch):
    """Point the media store at a temp directory for every test."""
    config_dir = tmp_path / "config"
    media_root = config_dir / "media"
    monkeypatch.setattr(media, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(media, "_MEDIA_DIR", media_root)
    return media_root


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestMediaDir:
    @posix_only
    def test_creates_directory_with_owner_only_permissions(self, tmp_media_dir):
        result = media.media_dir()

        assert result == tmp_media_dir
        assert result.is_dir()
        assert _mode(result) == 0o700

    @posix_only
    def test_fixes_permissions_on_already_existing_directory(self, tmp_media_dir):
        tmp_media_dir.mkdir(parents=True, mode=0o777)

        media.media_dir()

        assert _mode(tmp_media_dir) == 0o700


class TestSaveMedia:
    @posix_only
    def test_copied_file_is_owner_only(self, tmp_path, tmp_media_dir):
        src = tmp_path / "photo.jpg"
        src.write_bytes(b"data")

        dest = media.save_media(str(src), "w:msg1")

        assert dest.read_bytes() == b"data"
        assert _mode(dest) == 0o600

    @pytest.mark.parametrize(
        "msg_id",
        [
            "..\\outside:evil",
            "../../etc/passwd",
            "../../../etc/passwd",
            "C:\\evil",
        ],
    )
    def test_traversal_style_ids_stay_inside_media_dir(self, tmp_path, tmp_media_dir, msg_id):
        src = tmp_path / "photo.jpg"
        src.write_bytes(b"data")

        dest = media.save_media(str(src), msg_id)

        assert dest.is_relative_to(tmp_media_dir.resolve())
        assert dest.parent == tmp_media_dir.resolve()

"""
test_looks_like_filename.py — SyncExecutor._looks_like_filename heuristic
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from salt_and_soil.sync.executor import SyncExecutor


def test_accepts_plain_filename_with_extension():
    assert SyncExecutor._looks_like_filename("movie.mkv")
    assert SyncExecutor._looks_like_filename("archive.tar.gz")


def test_accepts_path_with_slash():
    assert SyncExecutor._looks_like_filename("subdir/movie.mkv")
    assert SyncExecutor._looks_like_filename("a/b/c/file")


def test_rejects_sending_line():
    assert not SyncExecutor._looks_like_filename("sending incremental file list")


def test_rejects_receiving_line():
    assert not SyncExecutor._looks_like_filename("receiving incremental file list")


def test_rejects_sent_and_total_summary():
    assert not SyncExecutor._looks_like_filename(
        "sent 1,234 bytes  received 56 bytes  2,580.00 bytes/sec"
    )
    assert not SyncExecutor._looks_like_filename(
        "total size is 1,234,567,890  speedup is 1.00"
    )


def test_rejects_created_and_building_line():
    assert not SyncExecutor._looks_like_filename("created directory /mnt/foo")
    assert not SyncExecutor._looks_like_filename("building file list...")


def test_rejects_rsync_error_and_delta():
    assert not SyncExecutor._looks_like_filename("rsync: connection unexpectedly closed")
    assert not SyncExecutor._looks_like_filename("rsync error: some error")
    assert not SyncExecutor._looks_like_filename("delta-transmission disabled for local transfer")


def test_rejects_plain_word_without_extension_or_slash():
    assert not SyncExecutor._looks_like_filename("Hello world")


def test_rejects_empty_string():
    assert not SyncExecutor._looks_like_filename("")


def test_case_insensitive_prefix_check():
    assert not SyncExecutor._looks_like_filename("Sending incremental file list")
    assert not SyncExecutor._looks_like_filename("SENT 1,234 bytes")

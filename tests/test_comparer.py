"""Test de comparer logica in detail."""
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from salt_and_soil.scanner.models import ScanSnapshot, ScanEntry
from salt_and_soil.sync.comparer import compare
from salt_and_soil.shared.enums import DiffStatus, SyncAction


def _snap(root: str, entries: list[tuple[str, int]], node: str = "local") -> ScanSnapshot:
    return ScanSnapshot(
        snapshot_id = "test",
        node_name   = node,
        sync_root   = root,
        scanned_at  = "2026-01-01T00:00:00",
        entries     = [
            ScanEntry(relative_path=name, entry_type="dir", size=size, mtime_utc=None)
            for name, size in entries
        ],
    )


def test_both_present_same_size():
    local  = _snap("v", [("films", 1000)])
    remote = _snap("v", [("films", 1000)], node="remote")
    diffs  = compare(local, remote)
    assert diffs[0].diff_status   == DiffStatus.IN_SYNC
    assert diffs[0].planned_action == SyncAction.SKIP


def test_both_present_size_diff():
    local  = _snap("v", [("films", 2000)])
    remote = _snap("v", [("films", 500)],  node="remote")
    diffs  = compare(local, remote)
    assert diffs[0].diff_status   == DiffStatus.NEEDS_SYNC
    assert diffs[0].planned_action == SyncAction.SYNC


def test_within_tolerance():
    """Minder dan 1% verschil = in sync."""
    local  = _snap("v", [("films", 10000)])
    remote = _snap("v", [("films", 10005)], node="remote")
    diffs  = compare(local, remote)
    assert diffs[0].diff_status == DiffStatus.IN_SYNC


def test_local_only():
    local  = _snap("v", [("films", 1000)])
    diffs  = compare(local, None)
    assert diffs[0].diff_status   == DiffStatus.LOCAL_ONLY
    assert diffs[0].planned_action == SyncAction.SYNC


def test_remote_only():
    local  = _snap("v", [])
    remote = _snap("v", [("films", 1000)], node="remote")
    diffs  = compare(local, remote)
    assert diffs[0].diff_status   == DiffStatus.REMOTE_ONLY
    assert diffs[0].planned_action == SyncAction.SKIP


def test_mixed():
    local  = _snap("v", [("a", 100), ("b", 200)])
    remote = _snap("v", [("b", 200), ("c", 300)], node="remote")
    diffs  = compare(local, remote)
    by_name = {d.name: d for d in diffs}
    assert by_name["a"].diff_status == DiffStatus.LOCAL_ONLY
    assert by_name["b"].diff_status == DiffStatus.IN_SYNC
    assert by_name["c"].diff_status == DiffStatus.REMOTE_ONLY


def test_sync_root_preserved():
    local = _snap("music", [("jazz", 500)])
    diffs = compare(local, None)
    assert diffs[0].sync_root == "music"

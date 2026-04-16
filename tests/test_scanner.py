"""
test_scanner.py - unit tests voor scanner, comparer en state
"""
import asyncio, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from salt_and_soil.scanner.scanner import DirScanner
from salt_and_soil.scanner.models import ScanSnapshot, ScanEntry
from salt_and_soil.sync.comparer import compare
from salt_and_soil.state.snapshots import SnapshotManager
from salt_and_soil.state.json_store import JSONStateStore
from salt_and_soil.shared.enums import DiffStatus, SyncAction


def _snap(root, dirs):
    entries = [ScanEntry(relative_path=n, entry_type="dir", size=s, mtime_utc=None) for n,s in dirs.items()]
    return ScanSnapshot(snapshot_id="t", node_name="n", sync_root=root, scanned_at="2026-01-01",
                        entries=entries, entry_count=len(entries), total_size=sum(dirs.values()))


def test_scanner_finds_dirs():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "videos"
        (root / "film-a").mkdir(parents=True)
        (root / "film-b").mkdir(parents=True)
        (root / "file.txt").write_text("x")
        scanner = DirScanner(tmp, ["videos"], "test")
        snaps = asyncio.run(scanner.scan_all())
        assert {e.relative_path for e in snaps[0].top_level_dirs()} == {"film-a", "film-b"}


def test_scanner_missing_root():
    with tempfile.TemporaryDirectory() as tmp:
        scanner = DirScanner(tmp, ["nonexistent"], "test")
        snaps = asyncio.run(scanner.scan_all())
        assert snaps[0].entry_count == 0


def test_compare_in_sync():
    diffs = compare(_snap("v", {"a": 1000}), _snap("v", {"a": 1000}))
    assert diffs[0].diff_status == DiffStatus.IN_SYNC


def test_compare_needs_sync():
    diffs = compare(_snap("v", {"a": 1_000_000}), _snap("v", {"a": 400_000}))
    assert diffs[0].diff_status == DiffStatus.NEEDS_SYNC
    assert diffs[0].planned_action == SyncAction.SYNC


def test_compare_local_only():
    diffs = compare(_snap("v", {"a": 1000}), _snap("v", {}))
    assert diffs[0].diff_status == DiffStatus.LOCAL_ONLY


def test_compare_remote_only():
    diffs = compare(_snap("v", {}), _snap("v", {"old": 5000}))
    assert diffs[0].diff_status == DiffStatus.REMOTE_ONLY
    assert diffs[0].planned_action == SyncAction.SKIP


def test_compare_no_remote():
    diffs = compare(_snap("v", {"a": 1000}), None)
    assert diffs[0].diff_status == DiffStatus.LOCAL_ONLY


def test_snapshot_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = SnapshotManager(tmp)
        mgr.save(_snap("videos", {"a": 100, "b": 200}))
        loaded = mgr.load_latest("videos")
        assert loaded.entry_count == 2
        assert {e.relative_path for e in loaded.entries} == {"a", "b"}


def test_state_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        store = JSONStateStore(f"{tmp}/state.json")
        sf = store.load("my-node", "orchestrator")
        sf.last_scan_at = "2026-01-01T12:00:00"
        store.save(sf)
        sf2 = store.load("my-node", "orchestrator")
        assert sf2.last_scan_at == "2026-01-01T12:00:00"

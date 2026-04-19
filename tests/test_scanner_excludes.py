"""
test_scanner_excludes.py — DirScanner filters top-level entries via fnmatch
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from salt_and_soil.scanner.scanner import DirScanner


def test_is_excluded_matches_literal():
    s = DirScanner(mount_point="/tmp", sync_roots=[], node_name="n", excludes=["@eaDir"])
    assert s._is_excluded("@eaDir")
    assert not s._is_excluded("videos")


def test_is_excluded_matches_glob():
    s = DirScanner(mount_point="/tmp", sync_roots=[], node_name="n",
                   excludes=["*@SynoEAStream", "*@SynoResource"])
    assert s._is_excluded("Movie@SynoEAStream")
    assert s._is_excluded("Show@SynoResource")
    assert not s._is_excluded("Movie")


def test_is_excluded_empty_list():
    s = DirScanner(mount_point="/tmp", sync_roots=[], node_name="n", excludes=[])
    assert not s._is_excluded("@eaDir")
    assert not s._is_excluded(".DS_Store")


def test_is_excluded_none_defaults_to_empty():
    s = DirScanner(mount_point="/tmp", sync_roots=[], node_name="n")
    assert s.excludes == []
    assert not s._is_excluded("anything")


def test_scan_filters_excluded_top_level(tmp_path):
    root = tmp_path / "videos"
    root.mkdir()
    (root / "Movie").mkdir()
    (root / "@eaDir").mkdir()
    (root / ".DS_Store").mkdir()

    s = DirScanner(
        mount_point = str(tmp_path),
        sync_roots  = ["videos"],
        node_name   = "test",
        excludes    = ["@eaDir", ".DS_Store"],
    )
    snap = asyncio.run(s.scan_root("videos"))
    names = [e.relative_path for e in snap.entries]
    assert names == ["Movie"]

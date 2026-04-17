"""
Scans a local directory and returns a ScanSnapshot.
Works recursively but only stores top-level entries for v1.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from .models import ScanEntry, ScanSnapshot
from ..shared.clock import utc_now_iso, snapshot_id


class DirScanner:
    def __init__(self, mount_point: str, sync_roots: list[str], node_name: str):
        self.mount_point = Path(mount_point)
        self.sync_roots  = sync_roots
        self.node_name   = node_name

    async def scan_all(self) -> list[ScanSnapshot]:
        snapshots = []
        for root in self.sync_roots:
            snap = await self.scan_root(root)
            snapshots.append(snap)
        return snapshots

    async def scan_root(self, sync_root: str) -> ScanSnapshot:
        sid       = snapshot_id()
        root_path = self.mount_point / sync_root
        snap = ScanSnapshot(
            snapshot_id = sid,
            node_name   = self.node_name,
            sync_root   = sync_root,
            scanned_at  = utc_now_iso(),
        )

        if not root_path.exists():
            snap.error = f"Path does not exist: {root_path}"
            return snap

        entries = []
        _SKIP = {"@eaDir", "@recycle", "#recycle", ".DS_Store"}

        try:
            for entry in sorted(root_path.iterdir(), key=lambda e: e.name):
                if entry.is_symlink() or not entry.is_dir():
                    continue
                if entry.name in _SKIP or entry.name.startswith("@"):
                    continue
                size  = await self._dir_size(entry)
                mtime = await self._mtime(entry)
                entries.append(ScanEntry(
                    relative_path = entry.name,
                    entry_type    = "dir",
                    size          = size,
                    mtime_utc     = mtime,
                ))
        except PermissionError as e:
            snap.error = str(e)

        snap.entries     = entries
        snap.entry_count = len(entries)
        snap.total_size  = sum(e.size for e in entries)
        return snap

    async def _dir_size(self, path: Path) -> int:
        proc = await asyncio.create_subprocess_exec(
            "du", "-sb", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        try:
            return int(stdout.split()[0])
        except (IndexError, ValueError):
            return 0

    async def _mtime(self, path: Path) -> datetime | None:
        try:
            ts = path.stat().st_mtime
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except OSError:
            return None

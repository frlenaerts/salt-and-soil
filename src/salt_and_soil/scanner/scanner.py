"""
Scans a local directory and returns a ScanSnapshot.
Works recursively but only stores top-level entries for v1.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .models import ScanEntry, ScanSnapshot
from ..shared.clock import utc_now_iso, snapshot_id


class DirScanner:
    def __init__(self, mount_point: str, sync_roots: list[str], node_name: str):
        self.mount_point = Path(mount_point)
        self.sync_roots  = sync_roots
        self.node_name   = node_name

    async def scan_all(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[ScanSnapshot]:
        # List all top-level dirs upfront so we know total count for progress
        dir_counts: list[tuple[str, list[Path]]] = []
        for root in self.sync_roots:
            root_path = self.mount_point / root
            try:
                dirs = sorted(
                    [e for e in root_path.iterdir() if not e.is_symlink() and e.is_dir()],
                    key=lambda e: e.name,
                )
            except (FileNotFoundError, PermissionError):
                dirs = []
            dir_counts.append((root, dirs))

        total = sum(len(d) for _, d in dir_counts)
        done  = 0
        snapshots = []
        for root, dirs in dir_counts:
            snap = await self._scan_root_with_progress(root, dirs, total, done, on_progress)
            done += len(dirs)
            snapshots.append(snap)
        return snapshots

    async def _scan_root_with_progress(
        self,
        sync_root: str,
        dirs: list[Path],
        total: int,
        done_before: int,
        on_progress: Callable[[int, int], None] | None,
    ) -> ScanSnapshot:
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
        try:
            for i, entry in enumerate(dirs):
                size  = await self._dir_size(entry)
                mtime = await self._mtime(entry)
                entries.append(ScanEntry(
                    relative_path = entry.name,
                    entry_type    = "dir",
                    size          = size,
                    mtime_utc     = mtime,
                ))
                if on_progress and total > 0:
                    on_progress(done_before + i + 1, total)
        except PermissionError as e:
            snap.error = str(e)

        snap.entries     = entries
        snap.entry_count = len(entries)
        snap.total_size  = sum(e.size for e in entries)
        return snap

    async def scan_root(
        self,
        sync_root: str,
        dirs: list[Path] | None = None,
        on_dir_done: Callable | None = None,
    ) -> ScanSnapshot:
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

        if dirs is None:
            try:
                dirs = sorted(
                    [e for e in root_path.iterdir() if not e.is_symlink() and e.is_dir()],
                    key=lambda e: e.name,
                )
            except PermissionError as e:
                snap.error = str(e)
                return snap

        entries = []
        try:
            for entry in dirs:
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

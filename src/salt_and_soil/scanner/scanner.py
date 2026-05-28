"""
Scans a local directory and returns a ScanSnapshot.
One level deep: the immediate subdirectories of `scan_path` become the entries.
"""
from __future__ import annotations

import asyncio
import fnmatch
from datetime import datetime, timezone
from pathlib import Path

from .models import ScanEntry, ScanSnapshot
from ..shared.clock import utc_now_iso, snapshot_id


class DirScanner:
    def __init__(
        self,
        node_name: str,
        excludes: list[str] | None = None,
    ):
        self.node_name = node_name
        self.excludes  = list(excludes or [])

    async def scan_source(self, scan_path: str | Path, alias: str) -> ScanSnapshot:
        sid = snapshot_id()
        path = Path(scan_path)
        snap = ScanSnapshot(
            snapshot_id  = sid,
            node_name    = self.node_name,
            source_alias = alias,
            scanned_at   = utc_now_iso(),
        )

        if not path.exists():
            snap.error = f"Path does not exist: {path}"
            return snap

        entries: list[ScanEntry] = []
        try:
            for entry in sorted(path.iterdir(), key=lambda e: e.name):
                if entry.is_symlink() or not entry.is_dir():
                    continue
                if self._is_excluded(entry.name):
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

    def _is_excluded(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, p) for p in self.excludes)

    async def _dir_size(self, path: Path) -> int:
        args = ["du", "-sb"]
        for p in self.excludes:
            args.append(f"--exclude={p}")
        args.append(str(path))
        proc = await asyncio.create_subprocess_exec(
            *args,
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

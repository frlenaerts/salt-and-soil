from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ScanEntry:
    relative_path: str          # POSIX relative path from sync_root
    entry_type: str             # "dir" | "file"
    size: int                   # bytes (recursive for dirs)
    mtime_utc: datetime | None
    exists: bool = True
    fingerprint_mode: str = "none"
    fingerprint_value: str = ""

    def size_hr(self) -> str:
        from ..shared.paths import human_size
        return human_size(self.size)


@dataclass
class ScanSnapshot:
    snapshot_id: str
    node_name: str
    sync_root: str
    scanned_at: str             # ISO string
    entries: list[ScanEntry] = field(default_factory=list)
    entry_count: int = 0
    total_size: int = 0
    error: str = ""

    def top_level_dirs(self) -> list[ScanEntry]:
        """Only direct children of sync_root (depth 1)."""
        return [e for e in self.entries if "/" not in e.relative_path and e.entry_type == "dir"]

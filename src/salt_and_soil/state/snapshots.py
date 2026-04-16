"""
Bewaart en laadt scan snapshots als JSON bestanden.
Eén bestand per scan run: data/state/snapshots/2026-04-16T21-30-00__videos.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..scanner.models import ScanSnapshot, ScanEntry
from ..shared.paths import ensure_dir


class SnapshotManager:
    def __init__(self, snapshot_dir: str):
        self.dir = Path(snapshot_dir)
        ensure_dir(self.dir)

    def save(self, snap: ScanSnapshot) -> Path:
        fname = f"{snap.snapshot_id}__{snap.sync_root.replace('/', '_')}.json"
        path  = self.dir / fname
        data = {
            "snapshot_id": snap.snapshot_id,
            "node_name":   snap.node_name,
            "sync_root":   snap.sync_root,
            "scanned_at":  snap.scanned_at,
            "entry_count": snap.entry_count,
            "total_size":  snap.total_size,
            "error":       snap.error,
            "entries": [
                {
                    "relative_path":    e.relative_path,
                    "entry_type":       e.entry_type,
                    "size":             e.size,
                    "mtime_utc":        e.mtime_utc.isoformat() if e.mtime_utc else None,
                    "fingerprint_mode": e.fingerprint_mode,
                    "fingerprint_value":e.fingerprint_value,
                }
                for e in snap.entries
            ],
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return path

    def load(self, path: Path) -> ScanSnapshot:
        raw   = json.loads(path.read_text())
        entries = []
        for e in raw.get("entries", []):
            mtime = None
            if e.get("mtime_utc"):
                try:
                    mtime = datetime.fromisoformat(e["mtime_utc"])
                except ValueError:
                    pass
            entries.append(ScanEntry(
                relative_path     = e["relative_path"],
                entry_type        = e["entry_type"],
                size              = e.get("size", 0),
                mtime_utc         = mtime,
                fingerprint_mode  = e.get("fingerprint_mode", "none"),
                fingerprint_value = e.get("fingerprint_value", ""),
            ))
        return ScanSnapshot(
            snapshot_id = raw["snapshot_id"],
            node_name   = raw["node_name"],
            sync_root   = raw["sync_root"],
            scanned_at  = raw["scanned_at"],
            entry_count = raw.get("entry_count", 0),
            total_size  = raw.get("total_size", 0),
            error       = raw.get("error", ""),
            entries     = entries,
        )

    def load_latest(self, sync_root: str) -> ScanSnapshot | None:
        pattern = f"*__{sync_root.replace('/', '_')}.json"
        files   = sorted(self.dir.glob(pattern))
        if not files:
            return None
        return self.load(files[-1])

    def list_snapshots(self) -> list[dict]:
        result = []
        for f in sorted(self.dir.glob("*.json"), reverse=True):
            try:
                raw = json.loads(f.read_text())
                result.append({
                    "file":        f.name,
                    "snapshot_id": raw.get("snapshot_id", ""),
                    "sync_root":   raw.get("sync_root", ""),
                    "scanned_at":  raw.get("scanned_at", ""),
                    "entry_count": raw.get("entry_count", 0),
                    "total_size":  raw.get("total_size", 0),
                })
            except Exception:
                continue
        return result

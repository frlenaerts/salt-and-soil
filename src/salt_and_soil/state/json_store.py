"""
Lichte JSON state store — lees/schrijf state.json.
Geen DB, geen ORM. Transparant en debugbaar.
"""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict

from .models import StateFile, SyncJob, FolderDiff
from ..shared.enums import DiffStatus, SyncAction, JobStatus
from ..shared.paths import ensure_dir


class JSONStateStore:
    def __init__(self, path: str):
        self.path = Path(path)
        ensure_dir(self.path.parent)

    def load(self, node_name: str, role: str) -> StateFile:
        if not self.path.exists():
            return StateFile(node_name=node_name, role=role)
        try:
            raw = json.loads(self.path.read_text())
            sf  = StateFile(
                node_name    = raw.get("node_name", node_name),
                role         = raw.get("role", role),
                last_scan_id = raw.get("last_scan_id", ""),
                last_scan_at = raw.get("last_scan_at", ""),
                last_sync_at = raw.get("last_sync_at", ""),
            )
            for j in raw.get("jobs", []):
                sf.jobs.append(SyncJob(
                    job_id    = j["job_id"],
                    sync_root = j["sync_root"],
                    folder    = j["folder"],
                    action    = SyncAction(j["action"]),
                    status    = JobStatus(j["status"]),
                    started_at  = j.get("started_at", ""),
                    finished_at = j.get("finished_at", ""),
                    error       = j.get("error", ""),
                    bytes_transferred = j.get("bytes_transferred", 0),
                ))
            for d in raw.get("diffs", []):
                sf.diffs.append(FolderDiff(
                    sync_root      = d["sync_root"],
                    name           = d["name"],
                    diff_status    = DiffStatus(d["diff_status"]),
                    local_size     = d.get("local_size", 0),
                    remote_size    = d.get("remote_size", 0),
                    planned_action = SyncAction(d.get("planned_action", "skip")),
                ))
            return sf
        except Exception:
            return StateFile(node_name=node_name, role=role)

    def save(self, state: StateFile) -> None:
        data = {
            "node_name":    state.node_name,
            "role":         state.role,
            "last_scan_id": state.last_scan_id,
            "last_scan_at": state.last_scan_at,
            "last_sync_at": state.last_sync_at,
            "jobs": [
                {
                    "job_id":    j.job_id,
                    "sync_root": j.sync_root,
                    "folder":    j.folder,
                    "action":    j.action.value,
                    "status":    j.status.value,
                    "started_at":  j.started_at,
                    "finished_at": j.finished_at,
                    "error":       j.error,
                    "bytes_transferred": j.bytes_transferred,
                }
                for j in state.jobs
            ],
            "diffs": [
                {
                    "sync_root":      d.sync_root,
                    "name":           d.name,
                    "diff_status":    d.diff_status.value,
                    "local_size":     d.local_size,
                    "remote_size":    d.remote_size,
                    "planned_action": d.planned_action.value,
                }
                for d in state.diffs
            ],
        }
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

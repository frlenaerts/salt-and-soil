"""
StateRepository combineert JSONStateStore + SnapshotManager.
De rest van de app praat enkel met de repository.
"""
from __future__ import annotations

from ..scanner.models import ScanSnapshot
from .json_store import JSONStateStore
from .snapshots import SnapshotManager
from .models import StateFile


class StateRepository:
    def __init__(self, state_file: str, snapshot_dir: str):
        self.store     = JSONStateStore(state_file)
        self.snapshots = SnapshotManager(snapshot_dir)

    def load_state(self, node_name: str, role: str) -> StateFile:
        return self.store.load(node_name, role)

    def save_state(self, state: StateFile) -> None:
        self.store.save(state)

    def save_snapshot(self, snap: ScanSnapshot):
        return self.snapshots.save(snap)

    def load_latest_snapshot(self, sync_root: str) -> ScanSnapshot | None:
        return self.snapshots.load_latest(sync_root)

    def list_snapshots(self) -> list[dict]:
        return self.snapshots.list_snapshots()

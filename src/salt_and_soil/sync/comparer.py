"""
Vergelijkt een lokale snapshot met een remote snapshot.
Geeft een lijst van FolderDiff terug.
"""
from __future__ import annotations

from ..scanner.models import ScanSnapshot
from ..state.models import FolderDiff
from ..shared.enums import DiffStatus, SyncAction


_SIZE_TOLERANCE = 0.01   # 1% verschil = nog als "in sync" beschouwen


def compare(
    local: ScanSnapshot,
    remote: ScanSnapshot | None,
) -> list[FolderDiff]:
    """
    local  = snapshot van orchestrator (master)
    remote = snapshot van agent (kan None zijn als nog niet gescand)
    """
    local_map  = {e.relative_path: e for e in local.entries if e.entry_type == "dir"}
    remote_map = {}
    if remote:
        remote_map = {e.relative_path: e for e in remote.entries if e.entry_type == "dir"}

    all_names = sorted(set(local_map.keys()) | set(remote_map.keys()))
    diffs = []

    for name in all_names:
        loc = local_map.get(name)
        rem = remote_map.get(name)

        if loc and rem:
            diff_pct = abs(loc.size - rem.size) / max(loc.size, 1)
            if diff_pct < _SIZE_TOLERANCE:
                status = DiffStatus.IN_SYNC
                action = SyncAction.SKIP
            else:
                status = DiffStatus.NEEDS_SYNC
                action = SyncAction.SYNC
        elif loc and not rem:
            status = DiffStatus.LOCAL_ONLY
            action = SyncAction.SYNC
        else:
            status = DiffStatus.REMOTE_ONLY
            action = SyncAction.SKIP

        diffs.append(FolderDiff(
            sync_root      = local.sync_root,
            name           = name,
            diff_status    = status,
            local_size     = loc.size  if loc else 0,
            remote_size    = rem.size  if rem else 0,
            planned_action = action,
        ))

    return diffs

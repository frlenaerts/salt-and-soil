"""
Voert een scan uit en print de resultaten — handig voor debugging
zonder de volledige web server te starten.
"""
from __future__ import annotations
import asyncio
from ..config import load as load_config
from ..scanner.scanner import DirScanner
from ..state.repository import StateRepository
from ..shared.paths import human_size


async def scan_and_print(config_path: str | None = None, roots: list[str] | None = None):
    cfg     = load_config(config_path)
    targets = roots or cfg.sync.sync_roots
    scanner = DirScanner(
        mount_point = cfg.mount.local_mount_path,
        sync_roots  = targets,
        node_name   = cfg.app.node_name,
    )
    repo = StateRepository(cfg.state.state_file, cfg.state.snapshot_dir)

    print(f"\nSalt & Soil — scan [{cfg.app.node_name}]")
    print(f"Mount: {cfg.mount.remote_host}:{cfg.mount.remote_share}")
    print(f"       → {cfg.mount.local_mount_path}\n")

    for snap in await scanner.scan_all():
        repo.save_snapshot(snap)
        dirs = snap.top_level_dirs()
        print(f"  /{snap.sync_root}  ({len(dirs)} mappen, {human_size(snap.total_size)})")
        for e in dirs:
            print(f"    {e.relative_path:<42} {e.size_hr():>10}")
        if snap.error:
            print(f"  ! Fout: {snap.error}")
        print()

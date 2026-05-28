"""
Runs a scan and prints the results — useful for debugging without starting the full web server.
"""
from __future__ import annotations
import asyncio
import posixpath

from ..config import load as load_config
from ..mounts.registry import MountRegistry
from ..scanner.scanner import DirScanner
from ..state.repository import StateRepository
from ..shared.paths import human_size


async def scan_and_print(config_path: str | None = None, aliases: list[str] | None = None):
    cfg      = load_config(config_path)
    registry = MountRegistry(cfg.mount_defaults, side="local")
    scanner  = DirScanner(cfg.app.node_name, cfg.sync.excludes)
    repo     = StateRepository(cfg.state.state_file, cfg.state.snapshot_dir)

    sources = [s for s in cfg.sources if aliases is None or s.alias in aliases]

    print(f"\nSalt & Soil — scan [{cfg.app.node_name}]")
    print(f"Sources: {', '.join(s.alias for s in sources)}\n")

    for src in sources:
        nfs       = registry.get_or_create(src.local_host, src.local_share)
        scan_path = posixpath.join(nfs.mount_point, src.local_path) if src.local_path else nfs.mount_point
        print(f"  {src.alias}: {src.local_host}:{src.local_share}/{src.local_path or ''}")
        print(f"           → {scan_path}")
        snap = await scanner.scan_source(scan_path, src.alias)
        repo.save_snapshot(snap)
        dirs = snap.top_level_dirs()
        print(f"  {src.alias}  ({len(dirs)} folders, {human_size(snap.total_size)})")
        for e in dirs:
            print(f"    {e.relative_path:<42} {e.size_hr():>10}")
        if snap.error:
            print(f"  ! Error: {snap.error}")
        print()

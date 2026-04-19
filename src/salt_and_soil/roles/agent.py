from __future__ import annotations

import asyncio
import fnmatch
import logging
from pathlib import Path

from ..config.models import Config
from ..mounts.nfs import NFSMount
from ..transport.dtos import DirEntry

log = logging.getLogger("salt-and-soil.agent")


class AgentRuntime:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.nfs = NFSMount(
            host        = cfg.mount.remote_host,
            share       = cfg.mount.remote_share,
            mount_point = cfg.mount.local_mount_path,
            nfs_version = cfg.mount.nfs_version,
            nfs_options = cfg.mount.nfs_options,
            retry_delay = cfg.mount.mount_retry_delay,
        )

    def _is_excluded(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, p) for p in self.cfg.sync.excludes)

    async def scan_root(self, sync_root: str) -> list[DirEntry]:
        root_path = Path(self.cfg.mount.local_mount_path) / sync_root
        if not root_path.exists():
            log.warning(f"Sync root bestaat niet: {root_path}")
            return []

        names = [
            e.name for e in root_path.iterdir()
            if not e.is_symlink() and e.is_dir() and not self._is_excluded(e.name)
        ]
        sizes = await asyncio.gather(*[self._dir_size(root_path / n) for n in names])
        return sorted(
            [DirEntry(name=n, size_bytes=s) for n, s in zip(names, sizes)],
            key=lambda d: d.name,
        )

    async def _dir_size(self, path: Path) -> int:
        args = ["du", "-sb"]
        for p in self.cfg.sync.excludes:
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

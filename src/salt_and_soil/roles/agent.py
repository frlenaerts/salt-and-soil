from __future__ import annotations

import asyncio
import fnmatch
import logging
import posixpath
from pathlib import Path

from ..config.models import Config, SourceConfig
from ..mounts.registry import MountRegistry
from ..mounts.nfs import NFSMount
from ..transport.dtos import DirEntry

log = logging.getLogger("salt-and-soil.agent")


class AgentRuntime:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.registry = MountRegistry(cfg.mount_defaults, side="local")
        self.sources_by_alias: dict[str, SourceConfig] = {s.alias: s for s in cfg.sources}

    def _source(self, alias: str) -> SourceConfig:
        try:
            return self.sources_by_alias[alias]
        except KeyError:
            raise KeyError(
                f"Unknown source alias '{alias}' "
                f"(known: {sorted(self.sources_by_alias.keys())})"
            )

    def mount_for(self, alias: str) -> NFSMount:
        src = self._source(alias)
        return self.registry.get_or_create(src.local_host, src.local_share)

    def scan_path_for(self, alias: str) -> str:
        src = self._source(alias)
        mp  = self.mount_for(alias).mount_point
        return posixpath.join(mp, src.local_path) if src.local_path else mp

    def _is_excluded(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, p) for p in self.cfg.sync.excludes)

    async def list_alias(self, alias: str) -> list[DirEntry]:
        scan_path = Path(self.scan_path_for(alias))
        if not scan_path.exists():
            log.warning(f"Scan path does not exist: {scan_path}")
            return []
        names = [
            e.name for e in scan_path.iterdir()
            if not e.is_symlink() and e.is_dir() and not self._is_excluded(e.name)
        ]
        sizes = await asyncio.gather(*[self._dir_size(scan_path / n) for n in names])
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

"""
MountRegistry — deduplicates NFSMount instances by (host, share).

Two [[sources]] pointing at the same NFS export share one underlying mount;
the registry hands back the same NFSMount for both. Mount points are derived
from a slug of host+share so they are stable and debuggable.
"""
from __future__ import annotations

import posixpath
import re

from .nfs import NFSMount
from ..config.models import MountDefaults


_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _slug(s: str) -> str:
    """Stable filesystem-safe slug. e.g. '192.168.1.100' → '192-168-1-100'."""
    return _SLUG_RE.sub("-", s).strip("-").lower() or "x"


class MountRegistry:
    """Owns NFSMount instances keyed on (host, share).

    Use `get_or_create(host, share)` to fetch a mount — repeat calls with the
    same pair return the same instance. Iterate via `all()` to mount/unmount
    everything (e.g. on orchestrator startup/shutdown).
    """

    def __init__(self, defaults: MountDefaults, *, side: str = "local"):
        """`side` selects which mount root from defaults to use:
        'local'  → defaults.mount_root_local
        'remote' → defaults.mount_root_remote
        """
        if side not in ("local", "remote"):
            raise ValueError(f"side must be 'local' or 'remote', got {side!r}")
        self.defaults = defaults
        self.side = side
        self._mounts: dict[tuple[str, str], NFSMount] = {}

    @property
    def mount_root(self) -> str:
        return self.defaults.mount_root_local if self.side == "local" \
            else self.defaults.mount_root_remote

    def get_or_create(self, host: str, share: str) -> NFSMount:
        key = (host, share)
        if key in self._mounts:
            return self._mounts[key]
        mp = posixpath.join(self.mount_root, f"{_slug(host)}_{_slug(share)}")
        m = NFSMount(
            host        = host,
            share       = share,
            mount_point = mp,
            nfs_version = self.defaults.nfs_version,
            nfs_options = self.defaults.nfs_options,
            retry_delay = self.defaults.mount_retry_delay,
        )
        self._mounts[key] = m
        return m

    def get(self, host: str, share: str) -> NFSMount | None:
        return self._mounts.get((host, share))

    def all(self) -> list[NFSMount]:
        return list(self._mounts.values())

    def __len__(self) -> int:
        return len(self._mounts)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._mounts

"""
Pre-flight checks before scanning or syncing.
Prevents scanning an empty local path when NFS is not mounted.
"""
from pathlib import Path
from .models import MountInfo
from ..shared.enums import MountStatus


class MountCheckError(Exception):
    pass


def assert_mount_ok(info: MountInfo) -> None:
    """Raises MountCheckError with a clear message if mount is not healthy."""
    if info.status != MountStatus.MOUNTED:
        raise MountCheckError(
            f"Mount not active: {info.source} → {info.local_path} "
            f"(status: {info.status.value})"
        )
    if not Path(info.local_path).exists():
        raise MountCheckError(
            f"Mount path does not exist: {info.local_path}"
        )
    if not info.writable:
        raise MountCheckError(
            f"Mount is read-only or not writable: {info.local_path}"
        )
    if info.error:
        raise MountCheckError(f"Mount error: {info.error}")


def is_path_empty(path: str) -> bool:
    """True if a directory is empty — warning signal that the mount may have failed."""
    p = Path(path)
    if not p.exists():
        return True
    return not any(p.iterdir())

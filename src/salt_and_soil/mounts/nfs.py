import asyncio
import os
from pathlib import Path

from .models import MountInfo
from ..shared.enums import MountStatus
from ..shared.clock import utc_now


async def _run(*cmd: str, env: dict | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


class NFSMount:
    def __init__(self, host: str, share: str, mount_point: str,
                 nfs_version: int = 3, nfs_options: str = "soft,timeo=30,retrans=3"):
        self.host        = host
        self.share       = share
        self.mount_point = mount_point
        self.nfs_version = nfs_version
        self.nfs_options = nfs_options

    async def is_mounted(self) -> bool:
        rc, _, _ = await _run("mountpoint", "-q", self.mount_point)
        return rc == 0

    async def mount(self) -> MountInfo:
        Path(self.mount_point).mkdir(parents=True, exist_ok=True)

        if await self.is_mounted():
            return await self.info()

        opts = f"vers={self.nfs_version},{self.nfs_options}"
        rc, _, err = await _run(
            "mount", "-t", "nfs", "-o", opts,
            f"{self.host}:{self.share}", self.mount_point,
        )
        if rc != 0:
            return MountInfo(
                remote_host=self.host, remote_share=self.share,
                local_path=self.mount_point,
                status=MountStatus.ERROR, error=err.strip(),
                last_checked_at=utc_now(),
            )
        return await self.info()

    async def unmount(self) -> bool:
        if not await self.is_mounted():
            return True
        rc, _, _ = await _run("umount", "-l", self.mount_point)
        return rc == 0

    async def info(self) -> MountInfo:
        mounted = await self.is_mounted()
        info = MountInfo(
            remote_host=self.host, remote_share=self.share,
            local_path=self.mount_point,
            status=MountStatus.MOUNTED if mounted else MountStatus.UNMOUNTED,
            last_checked_at=utc_now(),
        )
        if not mounted:
            return info

        # Read/write check
        test_file = Path(self.mount_point) / ".saltsoil_rw_check"
        try:
            test_file.write_text("ok")
            test_file.unlink()
            info.writable = True
        except OSError:
            info.writable = False

        # Disk usage — LANG=C ensures consistent numeric formatting across locales
        lc_env = {**os.environ, "LANG": "C", "LC_ALL": "C"}
        rc, out, _ = await _run("df", "-B1", self.mount_point, env=lc_env)
        if rc == 0:
            try:
                parts = out.strip().splitlines()[-1].split()
                info.total_bytes = int(parts[1])
                info.free_bytes  = int(parts[3])
            except (IndexError, ValueError):
                pass

        return info

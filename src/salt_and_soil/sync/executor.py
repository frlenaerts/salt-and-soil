from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

from ..state.models import SyncJob
from ..shared.enums import SyncAction, JobStatus
from ..shared.clock import utc_now_iso


class SyncExecutor:
    def __init__(
        self,
        local_mount:  str,
        remote_host:  str,
        remote_user:  str,
        remote_mount: str,
        ssh_key_file: str,
    ):
        self.local_mount  = local_mount
        self.remote_host  = remote_host
        self.remote_user  = remote_user
        self.remote_mount = remote_mount
        self.ssh_key_file = ssh_key_file

    @property
    def _ssh_opts(self) -> str:
        return (
            f"ssh -i {self.ssh_key_file} "
            "-o StrictHostKeyChecking=no "
            "-o ConnectTimeout=10"
        )

    async def execute(self, job: SyncJob) -> AsyncIterator[str]:
        job.status     = JobStatus.RUNNING
        job.started_at = utc_now_iso()
        try:
            if job.action == SyncAction.SYNC:
                async for line in self._rsync(job):
                    yield line
            elif job.action == SyncAction.DELETE_REMOTE:
                async for line in self._delete_remote(job):
                    yield line
            job.status      = JobStatus.DONE
            job.finished_at = utc_now_iso()
        except Exception as e:
            job.status      = JobStatus.FAILED
            job.error       = str(e)
            job.finished_at = utc_now_iso()
            yield f"FOUT: {e}"

    async def _rsync(self, job: SyncJob) -> AsyncIterator[str]:
        src = os.path.join(self.local_mount, job.sync_root, job.folder) + "/"
        dst = (
            f"{self.remote_user}@{self.remote_host}:"
            f"{self.remote_mount}/{job.sync_root}/{job.folder}/"
        )
        cmd = [
            "rsync", "-avz", "--progress", "--partial",
            "-e", self._ssh_opts,
            src, dst,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                yield line
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"rsync exit {proc.returncode}")

    async def _delete_remote(self, job: SyncJob) -> AsyncIterator[str]:
        path = f"{self.remote_mount}/{job.sync_root}/{job.folder}"
        yield f"rm -rf {path}"
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-i", self.ssh_key_file,
            "-o", "StrictHostKeyChecking=no",
            f"{self.remote_user}@{self.remote_host}",
            f"rm -rf '{path}'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("delete mislukt")
        yield "Verwijderd ✓"

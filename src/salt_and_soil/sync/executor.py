from __future__ import annotations

import asyncio
import os
import re
import shlex
from typing import AsyncIterator

from ..state.models import SyncJob
from ..shared.enums import SyncAction, JobStatus
from ..shared.clock import utc_now_iso
from ..shared.paths import human_size


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
            f"ssh -i {shlex.quote(self.ssh_key_file)} "
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
            yield f"ERROR: {e}"

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
        current_file: str | None = None
        async for raw in proc.stdout:
            chunk = raw.decode(errors="replace")
            for part in re.split(r"[\r\n]+", chunk):
                part = part.strip()
                if not part:
                    continue
                if "%" in part:
                    if "100%" in part:
                        line = self._format_progress(current_file, part)
                        if line:
                            yield line
                else:
                    name = part.split("/")[-1]
                    if name and "." in name:
                        current_file = name
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"rsync exit {proc.returncode}")

    @staticmethod
    def _format_progress(filename: str | None, line: str) -> str | None:
        m = re.search(
            r"([\d,]+)\s+100%\s+([\d.]+\s*\S+/s).*xfr#(\d+).*to-chk=\d+/(\d+)",
            line,
        )
        if not m:
            return None
        size   = human_size(int(m.group(1).replace(",", "")))
        speed  = m.group(2)
        num    = m.group(3)
        total  = m.group(4)
        name   = filename or "?"
        return f"{name} — {size} — {speed} — ({num}/{total})"

    async def _delete_remote(self, job: SyncJob) -> AsyncIterator[str]:
        path = f"{self.remote_mount}/{job.sync_root}/{job.folder}"
        yield f"Deleting on {self.remote_host}: {path}"
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-i", self.ssh_key_file,
            "-o", "StrictHostKeyChecking=no",
            f"{self.remote_user}@{self.remote_host}",
            f"rm -rf {shlex.quote(path)}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("delete failed")
        yield "Deleted"

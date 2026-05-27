from __future__ import annotations
from dataclasses import dataclass, field
from ..shared.enums import DiffStatus, SyncAction, JobStatus


STATE_SCHEMA_VERSION = 2  # bumped from implicit v1 when sync_root → source_alias


@dataclass
class FolderDiff:
    """One folder seen on the local and/or remote side of a source.
    `source_alias` identifies which [[sources]] entry this belongs to."""
    source_alias: str
    name: str
    diff_status: DiffStatus
    local_size: int  = 0
    remote_size: int = 0
    planned_action: SyncAction = SyncAction.SKIP

    @property
    def local_size_hr(self) -> str:
        from ..shared.paths import human_size
        return human_size(self.local_size)

    @property
    def remote_size_hr(self) -> str:
        from ..shared.paths import human_size
        return human_size(self.remote_size)


@dataclass
class SyncJob:
    job_id: str
    source_alias: str
    folder: str
    action: SyncAction
    status: JobStatus = JobStatus.PENDING
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    bytes_transferred: int = 0


@dataclass
class StateFile:
    node_name: str
    role: str
    schema_version: int = STATE_SCHEMA_VERSION
    last_scan_id: str = ""
    last_scan_at: str = ""
    last_sync_at: str = ""
    jobs: list[SyncJob] = field(default_factory=list)
    diffs: list[FolderDiff] = field(default_factory=list)

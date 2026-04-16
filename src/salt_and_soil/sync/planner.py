from __future__ import annotations

import uuid
from ..state.models import FolderDiff, SyncJob
from ..shared.enums import SyncAction, JobStatus
from ..shared.clock import utc_now_iso


def build_jobs(diffs: list[FolderDiff]) -> list[SyncJob]:
    """Zet FolderDiffs met een niet-skip actie om naar SyncJobs."""
    jobs = []
    for diff in diffs:
        if diff.planned_action == SyncAction.SKIP:
            continue
        jobs.append(SyncJob(
            job_id    = str(uuid.uuid4())[:8],
            sync_root = diff.sync_root,
            folder    = diff.name,
            action    = diff.planned_action,
            status    = JobStatus.PENDING,
        ))
    return jobs

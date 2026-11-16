from __future__ import annotations

import uuid
from ..state.models import FolderDiff, SyncJob
from ..shared.enums import DiffStatus, SyncAction, JobStatus
from ..shared.clock import utc_now_iso


# Which actions make sense for a folder in a given state. These are the same
# choices the UI offers (ACTION_OPTS in index.html) — but the UI is only a
# convenience; this table is what the server enforces.
ALLOWED_ACTIONS: dict[DiffStatus, frozenset[SyncAction]] = {
    DiffStatus.IN_SYNC:     frozenset({SyncAction.SKIP, SyncAction.DELETE_REMOTE}),
    DiffStatus.NEEDS_SYNC:  frozenset({SyncAction.SYNC, SyncAction.SKIP, SyncAction.DELETE_REMOTE}),
    DiffStatus.LOCAL_ONLY:  frozenset({SyncAction.SYNC, SyncAction.SKIP}),
    DiffStatus.REMOTE_ONLY: frozenset({SyncAction.SKIP, SyncAction.PULL, SyncAction.DELETE_REMOTE}),
}


def is_action_allowed(status: DiffStatus, action: SyncAction) -> bool:
    """Skipping is always fine; anything else must be listed for the status."""
    return action == SyncAction.SKIP or action in ALLOWED_ACTIONS.get(status, frozenset())


def build_jobs(diffs: list[FolderDiff]) -> list[SyncJob]:
    """Converts FolderDiffs with a non-skip action into SyncJobs."""
    jobs = []
    for diff in diffs:
        if diff.planned_action == SyncAction.SKIP:
            continue
        jobs.append(SyncJob(
            job_id       = str(uuid.uuid4())[:8],
            source_alias = diff.source_alias,
            folder       = diff.name,
            action       = diff.planned_action,
            status       = JobStatus.PENDING,
        ))
    return jobs

from enum import Enum


class NodeRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    AGENT = "agent"


class MountStatus(str, Enum):
    MOUNTED = "mounted"
    UNMOUNTED = "unmounted"
    ERROR = "error"
    UNKNOWN = "unknown"


class CompareMode(str, Enum):
    SIZE_MTIME = "size_mtime"
    CHECKSUM = "checksum"


class SyncAction(str, Enum):
    SYNC = "sync"
    PULL = "pull"
    DELETE_REMOTE = "delete_remote"
    SKIP = "skip"


class DiffStatus(str, Enum):
    IN_SYNC = "in_sync"
    NEEDS_SYNC = "needs_sync"
    LOCAL_ONLY = "local_only"
    REMOTE_ONLY = "remote_only"
    UNKNOWN = "unknown"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class AppStatus(str, Enum):
    IDLE = "idle"
    MOUNTING = "mounting"
    SCANNING = "scanning"
    READY = "ready"
    SYNCING = "syncing"
    DONE = "done"
    ERROR = "error"

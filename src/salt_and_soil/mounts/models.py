from dataclasses import dataclass, field
from datetime import datetime
from ..shared.enums import MountStatus


@dataclass
class MountInfo:
    remote_host: str
    remote_share: str
    local_path: str
    status: MountStatus = MountStatus.UNKNOWN
    writable: bool = False
    total_bytes: int = 0
    free_bytes: int = 0
    last_checked_at: datetime | None = None
    error: str = ""

    @property
    def source(self) -> str:
        return f"{self.remote_host}:{self.remote_share}"

    @property
    def is_ok(self) -> bool:
        return self.status == MountStatus.MOUNTED and self.writable

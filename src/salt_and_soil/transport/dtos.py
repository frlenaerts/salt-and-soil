"""
Data Transfer Objects — plain Python dataclasses, geen pydantic vereist.
"""
from __future__ import annotations
from dataclasses import dataclass
from ..shared.enums import SyncAction


@dataclass
class ActionItem:
    sync_root: str
    folder:    str
    action:    SyncAction

    @classmethod
    def from_dict(cls, d: dict) -> "ActionItem":
        return cls(
            sync_root = d["sync_root"],
            folder    = d["folder"],
            action    = SyncAction(d["action"]),
        )


@dataclass
class ExecuteRequest:
    actions: list[ActionItem]

    @classmethod
    def from_dict(cls, d: dict) -> "ExecuteRequest":
        return cls(actions=[ActionItem.from_dict(a) for a in d.get("actions", [])])


@dataclass
class DirEntry:
    name:       str
    size_bytes: int

    def to_dict(self) -> dict:
        return {"name": self.name, "size_bytes": self.size_bytes}


@dataclass
class ListDirsResponse:
    sync_root: str
    dirs:      list[DirEntry]

    def to_dict(self) -> dict:
        return {"sync_root": self.sync_root, "dirs": [d.to_dict() for d in self.dirs]}


@dataclass
class MountResponse:
    ok:      bool
    mounted: bool
    msg:     str = ""
    error:   str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "mounted": self.mounted, "msg": self.msg, "error": self.error}


@dataclass
class StatusResponse:
    ok:          bool
    node_name:   str
    mounted:     bool
    mount_point: str
    nas_host:    str
    total_bytes: int = 0
    free_bytes:  int = 0
    error:       str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "node_name": self.node_name,
            "mounted": self.mounted, "mount_point": self.mount_point,
            "nas_host": self.nas_host, "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes, "error": self.error,
        }


@dataclass
class SnapshotMeta:
    file:        str
    snapshot_id: str
    sync_root:   str
    scanned_at:  str
    entry_count: int
    total_size:  int

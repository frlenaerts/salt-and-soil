"""
Data Transfer Objects — plain Python dataclasses, no pydantic required.
"""
from __future__ import annotations
from dataclasses import dataclass
from ..shared.enums import SyncAction


@dataclass
class ActionItem:
    source_alias: str
    folder:       str
    action:       SyncAction

    @classmethod
    def from_dict(cls, d: dict) -> "ActionItem":
        return cls(
            source_alias = d["source_alias"],
            folder       = d["folder"],
            action       = SyncAction(d["action"]),
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
    source_alias: str
    dirs:         list[DirEntry]

    def to_dict(self) -> dict:
        return {"source_alias": self.source_alias, "dirs": [d.to_dict() for d in self.dirs]}

    @classmethod
    def from_dict(cls, d: dict) -> "ListDirsResponse":
        return cls(
            source_alias = d["source_alias"],
            dirs         = [DirEntry(**e) for e in d.get("dirs", [])],
        )


@dataclass
class MountResponse:
    ok:          bool
    mounted:     bool
    msg:         str = ""
    error:       str = ""
    total_bytes: int = 0
    free_bytes:  int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "mounted": self.mounted,
            "msg": self.msg, "error": self.error,
            "total_bytes": self.total_bytes, "free_bytes": self.free_bytes,
        }


@dataclass
class StatusResponse:
    ok:        bool
    node_name: str
    mounts:    list[dict]   # [{alias, host, share, mount_point, mounted, total_bytes, free_bytes, error}]
    error:     str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "node_name": self.node_name,
            "mounts": self.mounts,
            "error": self.error,
        }


@dataclass
class SnapshotMeta:
    file:         str
    snapshot_id:  str
    source_alias: str
    scanned_at:   str
    entry_count:  int
    total_size:   int

"""
Data Transfer Objects — plain Python dataclasses, no pydantic required.
"""
from __future__ import annotations
from dataclasses import dataclass
from ..shared.enums import SyncAction
from ..shared.paths import is_plain_name


def _short(value: object) -> str:
    """repr() capped in length — for quoting untrusted values in error messages."""
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


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

    @classmethod
    def from_dict(cls, d: object) -> "DirEntry":
        """Parse one entry received from an agent. The name later becomes a
        path component in rsync and delete commands, so anything that is not
        a plain directory name is rejected (ValueError). Unknown keys are
        ignored."""
        if not isinstance(d, dict):
            raise ValueError("directory entry is not an object")
        name, size = d.get("name"), d.get("size_bytes")
        if not is_plain_name(name):
            raise ValueError(f"folder name {_short(name)} is not a plain directory name")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"size of folder {_short(name)} is not a non-negative integer")
        return cls(name=name, size_bytes=size)


@dataclass
class ListDirsResponse:
    source_alias: str
    dirs:         list[DirEntry]

    def to_dict(self) -> dict:
        return {"source_alias": self.source_alias, "dirs": [d.to_dict() for d in self.dirs]}

    @classmethod
    def from_dict(cls, d: object) -> "ListDirsResponse":
        """Parse an agent /list response; raises ValueError if it is malformed."""
        if not isinstance(d, dict):
            raise ValueError("response is not an object")
        alias, dirs = d.get("source_alias"), d.get("dirs", [])
        if not isinstance(alias, str):
            raise ValueError("'source_alias' is missing")
        if not isinstance(dirs, list):
            raise ValueError("'dirs' is not a list")
        return cls(source_alias=alias, dirs=[DirEntry.from_dict(e) for e in dirs])


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

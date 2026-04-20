from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import Schedule
from ..shared.paths import ensure_dir

log = logging.getLogger("salt-and-soil.schedule")


class ScheduleStore:
    """JSON-backed persistence for a single Schedule."""

    def __init__(self, path: str):
        self.path = Path(path)
        ensure_dir(self.path.parent)

    def load(self) -> Schedule:
        if not self.path.exists():
            return Schedule()
        try:
            raw = json.loads(self.path.read_text())
            return Schedule(
                enabled = bool(raw.get("enabled", False)),
                days    = sorted({int(d) for d in raw.get("days", []) if 0 <= int(d) <= 6}),
                hour    = max(0, min(23, int(raw.get("hour", 3)))),
                minute  = max(0, min(59, int(raw.get("minute", 0)))),
            )
        except Exception as e:
            log.warning("Schedule file unreadable (%s), using defaults", e)
            return Schedule()

    def save(self, s: Schedule) -> None:
        self.path.write_text(json.dumps(s.to_dict(), indent=2))

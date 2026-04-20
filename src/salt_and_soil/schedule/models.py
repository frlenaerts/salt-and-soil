from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Schedule:
    """
    Weekly recurring trigger. `days` uses Python's Monday=0..Sunday=6 convention.
    When enabled, the scheduler fires once per matching (weekday, hour, minute)
    combination per day.
    """
    enabled: bool = False
    days:    list[int] = field(default_factory=list)
    hour:    int = 3
    minute:  int = 0

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "days":    sorted(set(self.days)),
            "hour":    self.hour,
            "minute":  self.minute,
        }

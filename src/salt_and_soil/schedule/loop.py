"""
ScheduleLoop: a lightweight asyncio task that polls every POLL_INTERVAL
seconds, reads the on-disk schedule, and fires runtime.run_scheduled_cycle()
when the current time matches. Missed firings (because runtime is busy)
are intentionally skipped until the next match — per project spec.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .models import Schedule

log = logging.getLogger("salt-and-soil.schedule")

POLL_INTERVAL = 30  # seconds


def should_fire(
    schedule: Schedule,
    now: datetime,
    last_fired_marker: tuple | None,
) -> bool:
    """
    Pure decision function (no runtime/IO). Returns True if the scheduler
    should fire at `now`, given the last-fired marker to prevent duplicate
    fires within the same minute.
    """
    if not schedule.enabled or not schedule.days:
        return False
    if now.weekday() not in schedule.days:
        return False
    if now.hour != schedule.hour or now.minute != schedule.minute:
        return False
    if _marker(now) == last_fired_marker:
        return False
    return True


def _marker(now: datetime) -> tuple:
    return (now.year, now.month, now.day, now.hour, now.minute)


class ScheduleLoop:
    def __init__(self, runtime):
        self.runtime = runtime
        self._task: asyncio.Task | None = None
        self._last_fired_marker: tuple | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="schedule-loop")
        log.info("Schedule loop started (poll every %ss)", POLL_INTERVAL)

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("schedule tick failed: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _tick(self) -> None:
        schedule = self.runtime.get_schedule()
        now = datetime.now()
        if not should_fire(schedule, now, self._last_fired_marker):
            return

        self._last_fired_marker = _marker(now)

        # Skip firings while any manual operation is in progress.
        from ..shared.enums import AppStatus
        if self.runtime.status not in (AppStatus.IDLE, AppStatus.READY, AppStatus.DONE):
            log.info("Scheduled firing skipped — runtime busy (%s)", self.runtime.status.value)
            return

        await self.runtime.run_scheduled_cycle()

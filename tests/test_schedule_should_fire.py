"""
test_schedule_should_fire.py — pure-function tests for the trigger decision.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from salt_and_soil.schedule.models import Schedule
from salt_and_soil.schedule.loop import should_fire, _marker


# 2026-04-20 is a Monday (weekday == 0).
MON_0330 = datetime(2026, 4, 20, 3, 30)
MON_0331 = datetime(2026, 4, 20, 3, 31)
TUE_0330 = datetime(2026, 4, 21, 3, 30)


def test_disabled_schedule_never_fires():
    s = Schedule(enabled=False, days=[0, 1, 2, 3, 4, 5, 6], hour=3, minute=30)
    assert should_fire(s, MON_0330, None) is False


def test_no_days_selected_never_fires():
    s = Schedule(enabled=True, days=[], hour=3, minute=30)
    assert should_fire(s, MON_0330, None) is False


def test_matching_weekday_and_time_fires():
    s = Schedule(enabled=True, days=[0], hour=3, minute=30)
    assert should_fire(s, MON_0330, None) is True


def test_wrong_weekday_does_not_fire():
    s = Schedule(enabled=True, days=[0], hour=3, minute=30)
    assert should_fire(s, TUE_0330, None) is False


def test_wrong_minute_does_not_fire():
    s = Schedule(enabled=True, days=[0], hour=3, minute=30)
    assert should_fire(s, MON_0331, None) is False


def test_already_fired_this_minute_does_not_refire():
    s = Schedule(enabled=True, days=[0], hour=3, minute=30)
    marker = _marker(MON_0330)
    assert should_fire(s, MON_0330, marker) is False


def test_same_time_next_week_fires_again():
    s = Schedule(enabled=True, days=[0], hour=3, minute=30)
    marker = _marker(MON_0330)
    next_mon = datetime(2026, 4, 27, 3, 30)
    assert should_fire(s, next_mon, marker) is True


def test_multiple_days_all_match():
    s = Schedule(enabled=True, days=[0, 2, 4], hour=3, minute=30)
    mon = datetime(2026, 4, 20, 3, 30)
    wed = datetime(2026, 4, 22, 3, 30)
    fri = datetime(2026, 4, 24, 3, 30)
    tue = datetime(2026, 4, 21, 3, 30)
    assert should_fire(s, mon, None) is True
    assert should_fire(s, wed, None) is True
    assert should_fire(s, fri, None) is True
    assert should_fire(s, tue, None) is False

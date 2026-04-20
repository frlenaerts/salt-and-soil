"""
test_schedule_store.py — JSON persistence of the Schedule model.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from salt_and_soil.schedule.models import Schedule
from salt_and_soil.schedule.store import ScheduleStore


def test_missing_file_returns_defaults(tmp_path):
    s = ScheduleStore(str(tmp_path / "schedule.json")).load()
    assert s == Schedule()
    assert s.enabled is False
    assert s.days == []


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "schedule.json"
    store = ScheduleStore(str(path))
    store.save(Schedule(enabled=True, days=[0, 2, 4], hour=3, minute=30))
    loaded = store.load()
    assert loaded.enabled is True
    assert loaded.days == [0, 2, 4]
    assert loaded.hour == 3
    assert loaded.minute == 30


def test_load_clamps_out_of_range_values(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text('{"enabled":true,"days":[-1,3,7,99],"hour":99,"minute":-5}')
    loaded = ScheduleStore(str(path)).load()
    assert loaded.days == [3]
    assert loaded.hour == 23
    assert loaded.minute == 0


def test_load_handles_corrupt_json(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text("not-json{{")
    loaded = ScheduleStore(str(path)).load()
    assert loaded == Schedule()


def test_save_dedupes_days(tmp_path):
    path = tmp_path / "schedule.json"
    ScheduleStore(str(path)).save(Schedule(enabled=True, days=[1, 1, 3, 3, 5], hour=2, minute=0))
    loaded = ScheduleStore(str(path)).load()
    assert loaded.days == [1, 3, 5]

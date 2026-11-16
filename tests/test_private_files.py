"""
test_private_files.py — files that hold secrets or state are owner-only and
are replaced atomically.

auth.toml contains the password hash and the session-signing secret: anyone
who can read it can mint a valid login cookie.
"""
import os
import stat
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from salt_and_soil import app as app_module
from salt_and_soil.auth.store import AuthStore
from salt_and_soil.config.loader import load
from salt_and_soil.scanner.models import ScanSnapshot
from salt_and_soil.schedule.models import Schedule
from salt_and_soil.schedule.store import ScheduleStore
from salt_and_soil.shared import paths as paths_module
from salt_and_soil.shared.paths import write_private
from salt_and_soil.state.json_store import JSONStateStore
from salt_and_soil.state.models import StateFile
from salt_and_soil.state.snapshots import SnapshotManager


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture(params=[0o022, 0o000], ids=["umask022", "umask000"])
def umask(request):
    old = os.umask(request.param)
    yield request.param
    os.umask(old)


# ── write_private ─────────────────────────────────────────────────────────────

def test_write_private_is_owner_only_whatever_the_umask(tmp_path, umask):
    target = tmp_path / "secret.txt"
    write_private(target, "hello")
    assert target.read_text() == "hello"
    assert mode(target) == 0o600


def test_write_private_replaces_a_world_readable_file(tmp_path):
    target = tmp_path / "secret.txt"
    target.write_text("old")
    target.chmod(0o644)
    write_private(target, "new")
    assert target.read_text() == "new"
    assert mode(target) == 0o600


def test_write_private_leaves_no_temp_files(tmp_path):
    write_private(tmp_path / "a.json", "{}")
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]


def test_failed_write_keeps_the_previous_version(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    write_private(target, "good")

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(paths_module.os, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        write_private(target, "half-writ")
    assert target.read_text() == "good"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_write_private_handles_non_ascii(tmp_path):
    write_private(tmp_path / "x.json", "Été — 日本")
    assert (tmp_path / "x.json").read_text(encoding="utf-8") == "Été — 日本"


# ── stores ────────────────────────────────────────────────────────────────────

def test_auth_file_is_created_owner_only(tmp_path, umask):
    store = AuthStore(tmp_path / "data" / "auth.toml")
    store.create("frank", "supersecret")
    assert mode(store.path) == 0o600


def test_auth_file_stays_owner_only_after_password_change(tmp_path):
    store = AuthStore(tmp_path / "auth.toml")
    store.create("frank", "supersecret")
    store.change_password("another-secret")
    assert mode(store.path) == 0o600


def test_auth_file_from_an_older_version_is_tightened_on_load(tmp_path):
    path = tmp_path / "auth.toml"
    AuthStore(path).create("frank", "supersecret")
    path.chmod(0o644)                       # what older versions produced

    user = AuthStore(path).load()
    assert user.username == "frank"
    assert mode(path) == 0o600


def test_state_schedule_and_snapshot_files_are_owner_only(tmp_path, umask):
    state = JSONStateStore(str(tmp_path / "state" / "state.json"))
    state.save(StateFile(node_name="n", role="orchestrator"))
    assert mode(state.path) == 0o600

    sched = ScheduleStore(str(tmp_path / "state" / "schedule.json"))
    sched.save(Schedule(enabled=True, days=[0], hour=3, minute=0))
    assert mode(sched.path) == 0o600
    assert sched.load().enabled is True

    snap_path = SnapshotManager(str(tmp_path / "snaps")).save(ScanSnapshot(
        snapshot_id="2026-01-01T00-00-00", node_name="n", source_alias="Été", scanned_at="now",
    ))
    assert mode(snap_path) == 0o600


def test_state_roundtrips_non_ascii_names(tmp_path):
    from salt_and_soil.shared.enums import DiffStatus
    from salt_and_soil.state.models import FolderDiff
    store = JSONStateStore(str(tmp_path / "state.json"))
    store.save(StateFile(node_name="n", role="orchestrator",
                         diffs=[FolderDiff(source_alias="videos", name="Été 日本", diff_status=DiffStatus.IN_SYNC)]))
    assert store.load("n", "orchestrator").diffs[0].name == "Été 日本"


# ── data dir + config file ────────────────────────────────────────────────────

def _config(tmp_path: Path, api_key: str = "") -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(textwrap.dedent(f"""
        [app]
        role     = "agent"
        data_dir = "{tmp_path}/data"
        [auth]
        api_key = "{api_key}"
        [[sources]]
        alias       = "videos"
        local_host  = "192.168.1.100"
        local_share = "/volume1"
    """))
    return cfg


def test_new_data_dir_is_owner_only(tmp_path, umask):
    app_module._ensure_data_dirs(load(_config(tmp_path)))
    assert mode(tmp_path / "data") == 0o700


def test_existing_data_dir_keeps_its_mode(tmp_path):
    (tmp_path / "data").mkdir(mode=0o755)
    (tmp_path / "data").chmod(0o755)
    app_module._ensure_data_dirs(load(_config(tmp_path)))
    assert mode(tmp_path / "data") == 0o755


def test_warns_when_config_with_api_key_is_readable_by_others(tmp_path):
    cfg = _config(tmp_path, api_key="s3cret")
    cfg.chmod(0o644)
    msg = app_module.config_exposure_warning(cfg, load(cfg))
    assert msg and "chmod 600" in msg and "0644" in msg


def test_no_warning_for_private_config_or_config_without_keys(tmp_path):
    cfg = _config(tmp_path, api_key="s3cret")
    cfg.chmod(0o600)
    assert app_module.config_exposure_warning(cfg, load(cfg)) is None

    cfg = _config(tmp_path, api_key="")
    cfg.chmod(0o644)
    assert app_module.config_exposure_warning(cfg, load(cfg)) is None

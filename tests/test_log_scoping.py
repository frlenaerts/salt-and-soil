"""Per-user log / error / mount filtering: a user must never see activity for
sources they have no rights to (including what an admin syncs)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from fastapi.testclient import TestClient

from salt_and_soil.config.models import (
    Config, AppConfig, ServerConfig, MountDefaults, SyncConfig, StateConfig,
    SourceConfig, AuthConfig,
)
from salt_and_soil.shared.enums import NodeRole
from salt_and_soil.roles.orchestrator import OrchestratorRuntime
from salt_and_soil.transport.api_server import create_app


@pytest.fixture
def app_rt(tmp_path):
    sd = tmp_path / "state"
    cfg = Config(
        app=AppConfig(role=NodeRole.ORCHESTRATOR, node_name="o", data_dir=str(tmp_path)),
        server=ServerConfig(), mount_defaults=MountDefaults(), sync=SyncConfig(),
        state=StateConfig(state_file=str(sd / "s.json"), snapshot_dir=str(sd / "snap")),
        sources=[SourceConfig(alias="Projects"), SourceConfig(alias="Photos")],
        auth=AuthConfig(), agents=[],
    )
    rt = OrchestratorRuntime(cfg)
    return create_app(cfg, rt), rt


def _admin_and_bob(app):
    admin = TestClient(app)
    admin.post("/setup", data={"username": "admin", "password": "adminpass1", "password2": "adminpass1"})
    admin.post("/api/users", json={
        "username": "bob", "password": "bobpass12", "confirm_password": "bobpass12",
        "allowed_aliases": ["Projects"],
    })
    bob = TestClient(app)
    bob.post("/login", data={"username": "bob", "password": "bobpass12"})
    return admin, bob


def _seed_log(rt):
    rt._info("[o] Scanning 2 source(s)...")                    # global
    rt._info("[o] Projects: 10 folders", "Projects")           # Projects-scoped
    rt._info("[o] Photos: 99 folders", "Photos")               # Photos-scoped
    rt._append_log("[o]    sending photo_2024/ ...", "Photos") # rsync output for Photos


def test_user_only_sees_permitted_log_lines(app_rt):
    app, rt = app_rt
    admin, bob = _admin_and_bob(app)
    _seed_log(rt)

    admin_log = admin.get("/api/state").json()["log"]
    bob_log   = bob.get("/api/state").json()["log"]

    # Admin sees everything; Bob sees only the generic + Projects lines.
    assert any("Photos" in l for l in admin_log)
    assert len(bob_log) == 2
    assert any("Scanning 2 source(s)" in l for l in bob_log)
    assert any("Projects" in l for l in bob_log)
    assert not any("Photos" in l for l in bob_log)
    assert not any("photo_2024" in l for l in bob_log)


def test_error_banner_scoped_to_user(app_rt):
    app, rt = app_rt
    admin, bob = _admin_and_bob(app)
    rt._error = "Mount /vol/photos failed"
    rt._error_scope = frozenset({"Photos"})

    assert admin.get("/api/state").json()["error"] == "Mount /vol/photos failed"
    assert bob.get("/api/state").json()["error"] == ""


def test_mounts_scoped_to_user(app_rt):
    app, rt = app_rt
    admin, bob = _admin_and_bob(app)
    rt._mounts_info = [
        {"side": "local", "host": "h", "share": "/vol/photos", "aliases": ["Photos"], "mount_point": "/m"},
        {"side": "local", "host": "h", "share": "/vol/proj",   "aliases": ["Projects"], "mount_point": "/n"},
    ]
    admin_mounts = admin.get("/api/state").json()["mounts"]
    bob_mounts   = bob.get("/api/state").json()["mounts"]
    assert len(admin_mounts) == 2
    assert [m["share"] for m in bob_mounts] == ["/vol/proj"]


def test_state_never_leaks_error_scope_field(app_rt):
    app, rt = app_rt
    admin, _ = _admin_and_bob(app)
    # Internal scope bookkeeping must not be exposed to clients.
    assert "error_scope" not in admin.get("/api/state").json()

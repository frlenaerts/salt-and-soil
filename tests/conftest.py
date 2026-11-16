"""Shared fixtures for tests that exercise the web apps through HTTP."""
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from fastapi.testclient import TestClient

from salt_and_soil.auth.store import AuthStore
from salt_and_soil.config.loader import load
from salt_and_soil.roles.orchestrator import OrchestratorRuntime
from salt_and_soil.transport.api_server import create_app

TEST_USER     = "admin"
TEST_PASSWORD = "correct-horse-battery"


def write_orchestrator_config(tmp_path: Path, extra: str = "") -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(textwrap.dedent(f"""
        [app]
        role      = "orchestrator"
        node_name = "orch-test"
        data_dir  = "{tmp_path}/data"

        [mount_defaults]
        mount_root_local  = "{tmp_path}/mnt"
        mount_root_remote = "/mnt/salt-and-soil"

        [state]
        state_file   = "{tmp_path}/data/state/state.json"
        snapshot_dir = "{tmp_path}/data/state/snapshots"

        [[agents]]
        name = "agent-01"
        host = "127.0.0.1"
        port = 1

        [[sources]]
        alias        = "videos"
        agent        = "agent-01"
        local_host   = "192.168.1.100"
        local_share  = "/volume1"
        local_path   = "videos"
        remote_share = "/volume1"
        remote_path  = "videos"
    """) + textwrap.dedent(extra))
    return cfg


def make_orchestrator(tmp_path: Path, extra_config: str = "") -> SimpleNamespace:
    """Real orchestrator app + runtime on a throwaway data dir. Nothing is
    mounted or scanned unless a test calls the runtime itself."""
    cfg = load(write_orchestrator_config(tmp_path, extra_config))
    rt  = OrchestratorRuntime(cfg)
    app = create_app(cfg, rt)
    return SimpleNamespace(
        app=app, rt=rt, cfg=cfg,
        auth_file=Path(cfg.app.data_dir) / "auth.toml",
        client=lambda: TestClient(app, follow_redirects=False),
    )


@pytest.fixture
def orch(tmp_path):
    """Orchestrator with no user account yet (first-run state)."""
    return make_orchestrator(tmp_path)


@pytest.fixture
def orch_user(tmp_path):
    """Orchestrator with an existing account; `.login()` returns a signed-in client."""
    # The account must exist before the app starts — that is what an
    # established install looks like (no setup token is issued).
    AuthStore(tmp_path / "data" / "auth.toml").create(TEST_USER, TEST_PASSWORD)
    o = make_orchestrator(tmp_path)

    def login(remember: bool = False) -> TestClient:
        c = o.client()
        data = {"username": TEST_USER, "password": TEST_PASSWORD}
        if remember:
            data["remember"] = "1"
        r = c.post("/login", data=data)
        assert r.status_code == 303, r.text
        return c

    o.login = login
    return o

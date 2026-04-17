"""
test_config.py — unit tests for the config loader
"""
import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from salt_and_soil.config.loader import load
from salt_and_soil.shared.enums import NodeRole, CompareMode

ORCHESTRATOR_TOML = textwrap.dedent("""
    [app]
    role      = "orchestrator"
    node_name = "test-node"
    data_dir  = "/tmp/saltsoil-test"

    [server]
    host = "0.0.0.0"
    port = 9090

    [mount]
    enabled          = true
    remote_host      = "192.168.1.99"
    remote_share     = "/volume1"
    local_mount_path = "/mnt/test"

    [sync]
    compare_mode = "size_mtime"
    sync_roots   = ["videos", "music"]

    [state]
    state_file   = "/tmp/state.json"
    snapshot_dir = "/tmp/snapshots"

    [[agents]]
    name         = "agent-01"
    host         = "10.0.0.5"
    port         = 8081
    ssh_host     = "10.0.0.5"
    ssh_user     = "root"
    ssh_key_file = "/root/.ssh/saltsoil_key"
""")

AGENT_TOML = textwrap.dedent("""
    [app]
    role      = "agent"
    node_name = "agent-01"

    [server]
    port = 8081

    [mount]
    enabled          = true
    remote_host      = "192.168.2.99"
    remote_share     = "/volume1"
    local_mount_path = "/mnt/test"

    [sync]
    sync_roots = ["videos"]

    [state]
    state_file   = "/tmp/state.json"
    snapshot_dir = "/tmp/snapshots"
""")


def _write(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


def test_orchestrator_role():
    cfg = load(_write(ORCHESTRATOR_TOML))
    assert cfg.app.role == NodeRole.ORCHESTRATOR
    assert cfg.app.node_name == "test-node"
    assert cfg.server.port == 9090

def test_sync_roots():
    cfg = load(_write(ORCHESTRATOR_TOML))
    assert cfg.sync.sync_roots == ["videos", "music"]

def test_agents_parsed():
    cfg = load(_write(ORCHESTRATOR_TOML))
    assert len(cfg.agents) == 1
    assert cfg.agents[0].name == "agent-01"

def test_agent_role():
    cfg = load(_write(AGENT_TOML))
    assert cfg.app.role == NodeRole.AGENT
    assert len(cfg.agents) == 0

def test_compare_mode_default():
    cfg = load(_write(AGENT_TOML))
    assert cfg.sync.compare_mode == CompareMode.SIZE_MTIME

def test_missing_config_raises():
    with pytest.raises(FileNotFoundError):
        load("/tmp/does_not_exist_saltsoil.toml")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from salt_and_soil.config.loader import load
from salt_and_soil.shared.enums import NodeRole, CompareMode


FIXTURE_TOML = """
[app]
role      = "orchestrator"
node_name = "test-node"
data_dir  = "./data"
log_level = "DEBUG"

[server]
host = "127.0.0.1"
port = 9999

[mount]
enabled          = true
type             = "nfs"
remote_host      = "10.0.0.100"
remote_share     = "/volume1"
local_mount_path = "/mnt/test"
nfs_version      = 3

[sync]
sync_roots   = ["videos", "music"]
compare_mode = "size_mtime"

[state]
state_file   = "./data/state/state.json"
snapshot_dir = "./data/state/snapshots"

[[agents]]
name     = "agent-01"
host     = "10.0.0.2"
port     = 8081
ssh_host = "10.0.0.2"
ssh_user = "root"
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(FIXTURE_TOML)
    return load(p)


def test_app_role(cfg):
    assert cfg.app.role == NodeRole.ORCHESTRATOR


def test_app_node_name(cfg):
    assert cfg.app.node_name == "test-node"


def test_server_port(cfg):
    assert cfg.server.port == 9999


def test_mount_host(cfg):
    assert cfg.mount.remote_host == "10.0.0.100"


def test_sync_roots(cfg):
    assert cfg.sync.sync_roots == ["videos", "music"]


def test_compare_mode(cfg):
    assert cfg.sync.compare_mode == CompareMode.SIZE_MTIME


def test_agent_parsed(cfg):
    assert len(cfg.agents) == 1
    assert cfg.agents[0].name == "agent-01"
    assert cfg.agents[0].port == 8081

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

    [mount_defaults]
    nfs_version       = 3
    mount_root_local  = "/mnt/test"
    mount_root_remote = "/mnt/test"

    [sync]
    compare_mode = "size_mtime"

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

    [[sources]]
    alias        = "videos"
    sort         = 10
    agent        = "agent-01"
    local_host   = "192.168.1.99"
    local_share  = "/volume1"
    local_path   = "videos"
    remote_share = "/volume1"
    remote_path  = "videos"

    [[sources]]
    alias        = "music"
    sort         = 20
    agent        = "agent-01"
    local_host   = "192.168.1.99"
    local_share  = "/volume1"
    local_path   = "music"
    remote_share = "/volume1"
    remote_path  = "music"
""")

AGENT_TOML = textwrap.dedent("""
    [app]
    role      = "agent"
    node_name = "agent-01"

    [server]
    port = 8081

    [mount_defaults]
    mount_root_local  = "/mnt/test"
    mount_root_remote = "/mnt/test"

    [state]
    state_file   = "/tmp/state.json"
    snapshot_dir = "/tmp/snapshots"

    [[sources]]
    alias       = "videos"
    local_host  = "192.168.2.99"
    local_share = "/volume1"
    local_path  = "videos"
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


def test_sources_parsed():
    cfg = load(_write(ORCHESTRATOR_TOML))
    assert [s.alias for s in cfg.sources] == ["videos", "music"]
    assert cfg.sources[0].local_path == "videos"
    assert cfg.sources[1].local_path == "music"


def test_sources_sorted_by_sort_then_alias():
    toml = textwrap.dedent("""
        [app]
        role      = "orchestrator"
        node_name = "test"
        [server]
        port = 8080
        [state]
        state_file   = "/tmp/s.json"
        snapshot_dir = "/tmp/snap"
        [[agents]]
        name = "agent-01"
        [[sources]]
        alias = "zeta"
        sort  = 30
        agent = "agent-01"
        local_host = "10.0.0.1"
        local_share = "/x"
        [[sources]]
        alias = "alpha"
        sort  = 10
        agent = "agent-01"
        local_host = "10.0.0.1"
        local_share = "/x"
        [[sources]]
        alias = "mu"
        sort  = 20
        agent = "agent-01"
        local_host = "10.0.0.1"
        local_share = "/x"
    """)
    cfg = load(_write(toml))
    assert [s.alias for s in cfg.sources] == ["alpha", "mu", "zeta"]


def test_sources_with_equal_sort_fall_back_to_alphabetical():
    toml = textwrap.dedent("""
        [app]
        role      = "orchestrator"
        node_name = "test"
        [server]
        port = 8080
        [state]
        state_file   = "/tmp/s.json"
        snapshot_dir = "/tmp/snap"
        [[agents]]
        name = "agent-01"
        [[sources]]
        alias = "beta"
        agent = "agent-01"
        local_host = "10.0.0.1"
        local_share = "/x"
        [[sources]]
        alias = "alpha"
        agent = "agent-01"
        local_host = "10.0.0.1"
        local_share = "/x"
    """)
    cfg = load(_write(toml))
    assert [s.alias for s in cfg.sources] == ["alpha", "beta"]


def test_agents_parsed():
    cfg = load(_write(ORCHESTRATOR_TOML))
    assert len(cfg.agents) == 1
    assert cfg.agents[0].name == "agent-01"


def test_agent_role_without_agents_block():
    cfg = load(_write(AGENT_TOML))
    assert cfg.app.role == NodeRole.AGENT
    assert len(cfg.agents) == 0
    assert len(cfg.sources) == 1
    assert cfg.sources[0].alias == "videos"


def test_compare_mode_default():
    cfg = load(_write(AGENT_TOML))
    assert cfg.sync.compare_mode == CompareMode.SIZE_MTIME


def test_missing_config_raises():
    with pytest.raises(FileNotFoundError):
        load("/tmp/does_not_exist_saltsoil.toml")


def test_legacy_mount_section_rejected():
    toml = textwrap.dedent("""
        [app]
        role      = "orchestrator"
        node_name = "test"
        [server]
        port = 8080
        [mount]
        remote_host  = "1.2.3.4"
        remote_share = "/v"
        [sync]
        sync_roots = ["videos"]
        [state]
        state_file   = "/tmp/s.json"
        snapshot_dir = "/tmp/snap"
    """)
    with pytest.raises(ValueError, match=r"\[mount\] is obsolete"):
        load(_write(toml))


def test_legacy_sync_roots_rejected():
    toml = textwrap.dedent("""
        [app]
        role      = "orchestrator"
        node_name = "test"
        [server]
        port = 8080
        [sync]
        sync_roots = ["videos"]
        [state]
        state_file   = "/tmp/s.json"
        snapshot_dir = "/tmp/snap"
        [[sources]]
        alias = "x"
        agent = "agent-01"
        local_host = "1.2.3.4"
        local_share = "/v"
    """)
    with pytest.raises(ValueError, match=r"sync_roots is obsolete"):
        load(_write(toml))


def test_duplicate_alias_rejected():
    toml = textwrap.dedent("""
        [app]
        role      = "orchestrator"
        node_name = "test"
        [server]
        port = 8080
        [state]
        state_file   = "/tmp/s.json"
        snapshot_dir = "/tmp/snap"
        [[agents]]
        name = "agent-01"
        [[sources]]
        alias = "dup"
        agent = "agent-01"
        local_host = "1.2.3.4"
        local_share = "/v"
        [[sources]]
        alias = "dup"
        agent = "agent-01"
        local_host = "1.2.3.4"
        local_share = "/v"
    """)
    with pytest.raises(ValueError, match=r"duplicate source alias"):
        load(_write(toml))


def test_unknown_agent_rejected():
    toml = textwrap.dedent("""
        [app]
        role      = "orchestrator"
        node_name = "test"
        [server]
        port = 8080
        [state]
        state_file   = "/tmp/s.json"
        snapshot_dir = "/tmp/snap"
        [[agents]]
        name = "agent-01"
        [[sources]]
        alias = "x"
        agent = "nonexistent"
        local_host = "1.2.3.4"
        local_share = "/v"
    """)
    with pytest.raises(ValueError, match=r"unknown agent 'nonexistent'"):
        load(_write(toml))


def test_local_path_traversal_rejected():
    toml = textwrap.dedent("""
        [app]
        role      = "orchestrator"
        node_name = "test"
        [server]
        port = 8080
        [state]
        state_file   = "/tmp/s.json"
        snapshot_dir = "/tmp/snap"
        [[agents]]
        name = "agent-01"
        [[sources]]
        alias = "x"
        agent = "agent-01"
        local_host = "1.2.3.4"
        local_share = "/v"
        local_path = "../etc"
    """)
    with pytest.raises(ValueError, match=r"local_path must not contain"):
        load(_write(toml))


def test_empty_sources_rejected():
    toml = textwrap.dedent("""
        [app]
        role      = "orchestrator"
        node_name = "test"
        [server]
        port = 8080
        [state]
        state_file   = "/tmp/s.json"
        snapshot_dir = "/tmp/snap"
    """)
    with pytest.raises(ValueError, match=r"\[\[sources\]\] must define"):
        load(_write(toml))

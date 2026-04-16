"""
Laadt een TOML config bestand en geeft een Config object terug.
Python 3.11+ heeft tomllib ingebouwd. Voor 3.10 gebruiken we tomli.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        raise ImportError("Install 'tomli' voor Python < 3.11:  pip install tomli")

from .models import (
    AppConfig, ServerConfig, OrchestratorRefConfig,
    MountConfig, SyncConfig, StateConfig, AgentConfig, Config,
)
from ..shared.enums import NodeRole, CompareMode

DEFAULT_CONFIG_PATH = os.getenv("SALTSOIL_CONFIG", "./config/config.toml")


def load(path: str | Path | None = None) -> Config:
    p = Path(path or DEFAULT_CONFIG_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Config niet gevonden: {p.resolve()}")

    with open(p, "rb") as f:
        raw = tomllib.load(f)

    app_raw  = raw.get("app", {})
    app = AppConfig(
        role       = NodeRole(app_raw.get("role", "orchestrator")),
        node_name  = app_raw.get("node_name", "node-01"),
        data_dir   = app_raw.get("data_dir", "./data"),
        log_level  = app_raw.get("log_level", "INFO"),
    )

    srv_raw = raw.get("server", {})
    server = ServerConfig(
        host = srv_raw.get("host", "0.0.0.0"),
        port = int(srv_raw.get("port", 8080)),
    )

    orc_raw = raw.get("orchestrator", {})
    orchestrator = OrchestratorRefConfig(
        host    = orc_raw.get("host", "127.0.0.1"),
        port    = int(orc_raw.get("port", 8080)),
        api_key = orc_raw.get("api_key", ""),
    )

    mnt_raw = raw.get("mount", {})
    mount = MountConfig(
        enabled          = mnt_raw.get("enabled", True),
        type             = mnt_raw.get("type", "nfs"),
        remote_host      = mnt_raw.get("remote_host", ""),
        remote_share     = mnt_raw.get("remote_share", ""),
        local_mount_path = mnt_raw.get("local_mount_path", "/mnt/salt-and-soil/source"),
        nfs_version      = int(mnt_raw.get("nfs_version", 3)),
        nfs_options      = mnt_raw.get("nfs_options", "soft,timeo=30,retrans=3"),
    )

    sync_raw = raw.get("sync", {})
    sync = SyncConfig(
        scan_on_startup  = sync_raw.get("scan_on_startup", False),
        auto_resume      = sync_raw.get("auto_resume", True),
        compare_mode     = CompareMode(sync_raw.get("compare_mode", "size_mtime")),
        max_parallel_jobs= int(sync_raw.get("max_parallel_jobs", 2)),
        sync_roots       = sync_raw.get("sync_roots", ["videos"]),
    )

    state_raw = raw.get("state", {})
    state = StateConfig(
        backend      = state_raw.get("backend", "json"),
        state_file   = state_raw.get("state_file", "./data/state/state.json"),
        snapshot_dir = state_raw.get("snapshot_dir", "./data/state/snapshots"),
    )

    agents = []
    for a in raw.get("agents", []):
        agents.append(AgentConfig(
            name             = a.get("name", "agent"),
            host             = a.get("host", ""),
            port             = int(a.get("port", 8081)),
            api_key          = a.get("api_key", ""),
            ssh_host         = a.get("ssh_host", ""),
            ssh_user         = a.get("ssh_user", "root"),
            ssh_key_file     = a.get("ssh_key_file", "/root/.ssh/saltsoil_key"),
            remote_mount_path= a.get("remote_mount_path", "/mnt/salt-and-soil/source"),
        ))

    return Config(
        app          = app,
        server       = server,
        orchestrator = orchestrator,
        mount        = mount,
        sync         = sync,
        state        = state,
        agents       = agents,
    )

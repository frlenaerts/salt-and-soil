"""
Loads a TOML config file and returns a Config object.
Python 3.11+ has tomllib built-in. For 3.10 we fall back to tomli.
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
        raise ImportError("Install 'tomli' for Python < 3.11:  pip install tomli")

from .models import (
    AppConfig, ServerConfig, AuthConfig, MountDefaults,
    SyncConfig, StateConfig, AgentConfig, SourceConfig, Config,
)
from ..shared.enums import NodeRole, CompareMode

DEFAULT_CONFIG_PATH = os.getenv("SALTSOIL_CONFIG", "./config/config.toml")


def load(path: str | Path | None = None) -> Config:
    p = Path(path or DEFAULT_CONFIG_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p.resolve()}")

    with open(p, "rb") as f:
        raw = tomllib.load(f)

    _reject_legacy(raw)

    app_raw = raw.get("app", {})
    _role_raw = app_raw.get("role", "orchestrator")
    try:
        _role = NodeRole(_role_raw)
    except ValueError:
        valid = [r.value for r in NodeRole]
        raise ValueError(f"Invalid app.role '{_role_raw}'. Valid values: {valid}")
    app = AppConfig(
        role      = _role,
        node_name = app_raw.get("node_name", "node-01"),
        data_dir  = app_raw.get("data_dir", "./data"),
        log_level = app_raw.get("log_level", "INFO"),
    )

    srv_raw = raw.get("server", {})
    server = ServerConfig(
        host = srv_raw.get("host", "0.0.0.0"),
        port = int(srv_raw.get("port", 8080)),
    )

    md_raw = raw.get("mount_defaults", {})
    mount_defaults = MountDefaults(
        type              = md_raw.get("type", "nfs"),
        nfs_version       = int(md_raw.get("nfs_version", 3)),
        nfs_options       = md_raw.get("nfs_options", "soft,timeo=30,retrans=3"),
        mount_retry_delay = int(md_raw.get("mount_retry_delay", 10)),
        mount_root_local  = md_raw.get("mount_root_local", "/mnt/salt-and-soil"),
        mount_root_remote = md_raw.get("mount_root_remote", "/mnt/salt-and-soil"),
    )

    sync_raw = raw.get("sync", {})
    _mode_raw = sync_raw.get("compare_mode", "size_mtime")
    try:
        _mode = CompareMode(_mode_raw)
    except ValueError:
        valid = [m.value for m in CompareMode]
        raise ValueError(f"Invalid sync.compare_mode '{_mode_raw}'. Valid values: {valid}")
    _exclude_file = sync_raw.get("exclude_file", "")
    _excludes: list[str] = []
    if _exclude_file:
        _ep = Path(_exclude_file)
        if _ep.exists():
            for ln in _ep.read_text(encoding="utf-8").splitlines():
                s = ln.strip()
                if s and not s.startswith("#"):
                    _excludes.append(s)
    sync = SyncConfig(
        scan_on_startup   = sync_raw.get("scan_on_startup", False),
        auto_resume       = sync_raw.get("auto_resume", True),
        compare_mode      = _mode,
        max_parallel_jobs = int(sync_raw.get("max_parallel_jobs", 2)),
        exclude_file      = _exclude_file,
        excludes          = _excludes,
    )

    state_raw = raw.get("state", {})
    state = StateConfig(
        backend      = state_raw.get("backend", "json"),
        state_file   = state_raw.get("state_file", "./data/state/state.json"),
        snapshot_dir = state_raw.get("snapshot_dir", "./data/state/snapshots"),
    )

    auth_raw = raw.get("auth", {})
    auth = AuthConfig(api_key=auth_raw.get("api_key", ""))

    agents: list[AgentConfig] = []
    for a in raw.get("agents", []):
        agents.append(AgentConfig(
            name         = a.get("name", "agent"),
            host         = a.get("host", ""),
            port         = int(a.get("port", 8081)),
            api_key      = a.get("api_key", ""),
            ssh_host     = a.get("ssh_host", ""),
            ssh_user     = a.get("ssh_user", "root"),
            ssh_key_file = a.get("ssh_key_file", "/root/.ssh/saltsoil_key"),
        ))

    sources = _parse_sources(raw.get("sources", []), app.role, agents)

    return Config(
        app            = app,
        server         = server,
        mount_defaults = mount_defaults,
        sync           = sync,
        state          = state,
        sources        = sources,
        auth           = auth,
        agents         = agents,
    )


def _reject_legacy(raw: dict) -> None:
    """Helpful errors when old config keys are still present."""
    if "mount" in raw:
        raise ValueError(
            "[mount] is obsolete. Define [mount_defaults] and move per-share "
            "settings into [[sources]] entries (local_host/local_share/local_path). "
            "See config.example.toml."
        )
    if "sync_roots" in raw.get("sync", {}):
        raise ValueError(
            "sync.sync_roots is obsolete. Each sync target is now a [[sources]] "
            "entry with its own alias. See config.example.toml."
        )
    for a in raw.get("agents", []):
        if "remote_share" in a or "remote_mount_path" in a:
            raise ValueError(
                f"agents.{a.get('name', '?')}: remote_share/remote_mount_path "
                f"are obsolete on [[agents]]. Move them into [[sources]] entries."
            )


def _parse_sources(
    raw_sources: list[dict],
    role: NodeRole,
    agents: list[AgentConfig],
) -> list[SourceConfig]:
    if not raw_sources:
        raise ValueError("[[sources]] must define at least one source")

    out: list[SourceConfig] = []
    seen: set[str] = set()
    for s in raw_sources:
        alias = s.get("alias", "").strip()
        if not alias:
            raise ValueError("[[sources]] entry missing 'alias'")
        if alias in seen:
            raise ValueError(f"duplicate source alias: '{alias}'")
        seen.add(alias)

        local_path = s.get("local_path", "")
        if ".." in Path(local_path).parts:
            raise ValueError(f"source '{alias}': local_path must not contain '..'")
        remote_path = s.get("remote_path", "")
        if ".." in Path(remote_path).parts:
            raise ValueError(f"source '{alias}': remote_path must not contain '..'")

        out.append(SourceConfig(
            alias        = alias,
            sort         = int(s.get("sort", 0)),
            agent        = s.get("agent", ""),
            local_host   = s.get("local_host", ""),
            local_share  = s.get("local_share", ""),
            local_path   = local_path,
            remote_share = s.get("remote_share", ""),
            remote_path  = remote_path,
        ))

    if role == NodeRole.ORCHESTRATOR:
        names = {a.name for a in agents}
        for src in out:
            if not src.agent:
                raise ValueError(
                    f"source '{src.alias}': 'agent' is required on an orchestrator node"
                )
            if src.agent not in names:
                raise ValueError(
                    f"source '{src.alias}': unknown agent '{src.agent}' "
                    f"(known: {sorted(names) or '<none>'})"
                )

    out.sort(key=lambda s: (s.sort, s.alias))
    return out

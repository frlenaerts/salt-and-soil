"""
Loads a TOML config file and returns a Config object.
Python 3.11+ has tomllib built-in. For 3.10 we fall back to tomli.
"""
from __future__ import annotations

import os
import posixpath
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
        host = _host(srv_raw.get("host", "0.0.0.0"), "server.host"),
        port = _port(srv_raw.get("port", 8080), "server.port"),
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
                if not s or s.startswith("#"):
                    continue
                # gitignore-style: '\#' at the start of a pattern is a literal '#'
                # (e.g. Synology's '#recycle' folder). Strip the leading backslash.
                if s.startswith("\\#"):
                    s = s[1:]
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
        name = a.get("name", "agent")
        agents.append(AgentConfig(
            name         = name,
            host         = _host(a.get("host", ""), f"agents.{name}: host"),
            port         = _port(a.get("port", 8081), f"agents.{name}: port"),
            api_key      = a.get("api_key", ""),
            ssh_host     = _host(a.get("ssh_host", ""), f"agents.{name}: ssh_host"),
            ssh_user     = _host(a.get("ssh_user", "root"), f"agents.{name}: ssh_user"),
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


MAX_ALIAS_LENGTH = 64


def _has_control_chars(value: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in value)


def _host(value: object, where: str) -> str:
    """Host names and user names become arguments of ssh, ping and mount.
    They may be empty (unset), but must not look like an option or contain
    anything a host or user name never has."""
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string")
    if value.startswith("-") or "/" in value or _has_control_chars(value) \
            or any(c.isspace() for c in value):
        raise ValueError(
            f"{where}: invalid value {value!r} (no spaces, no '/', no leading '-')"
        )
    return value


def _port(value: object, where: str) -> int:
    try:
        port = int(value)   # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{where} must be a number (got {value!r})")
    if isinstance(value, bool) or not 1 <= port <= 65535:
        raise ValueError(f"{where} must be between 1 and 65535 (got {value!r})")
    return port


def _share_path(value: object, alias: str, field: str) -> str:
    """Validate and normalise a source's local_path / remote_path: a path
    relative to the share ('' = the share root). An absolute path would
    replace the mount point altogether when the two are joined."""
    where = f"source '{alias}': {field}"
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string")
    if value.startswith("/"):
        raise ValueError(f"{where} must be relative to the share (no leading '/')")
    if ".." in Path(value).parts:
        raise ValueError(f"{where} must not contain '..'")
    if _has_control_chars(value):
        raise ValueError(f"{where} must not contain control characters")
    norm = posixpath.normpath(value) if value else ""
    return "" if norm == "." else norm


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
        alias = s.get("alias", "")
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("[[sources]] entry missing 'alias'")
        alias = alias.strip()
        if _has_control_chars(alias) or len(alias) > MAX_ALIAS_LENGTH:
            raise ValueError(
                f"source alias {alias[:MAX_ALIAS_LENGTH]!r}: at most {MAX_ALIAS_LENGTH} "
                f"characters, no control characters"
            )
        if alias in seen:
            raise ValueError(f"duplicate source alias: '{alias}'")
        seen.add(alias)

        shares = {}
        for field in ("local_share", "remote_share"):
            shares[field] = s.get(field, "")
            if not isinstance(shares[field], str) or _has_control_chars(shares[field]):
                raise ValueError(
                    f"source '{alias}': {field} must be a string without control characters"
                )

        out.append(SourceConfig(
            alias        = alias,
            sort         = int(s.get("sort", 0)),
            agent        = s.get("agent", ""),
            local_host   = _host(s.get("local_host", ""), f"source '{alias}': local_host"),
            local_share  = shares["local_share"],
            local_path   = _share_path(s.get("local_path", ""), alias, "local_path"),
            remote_share = shares["remote_share"],
            remote_path  = _share_path(s.get("remote_path", ""), alias, "remote_path"),
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

from dataclasses import dataclass, field
from ..shared.enums import NodeRole, CompareMode


@dataclass
class AppConfig:
    role: NodeRole
    node_name: str
    data_dir: str = "./data"
    log_level: str = "INFO"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class MountDefaults:
    type: str = "nfs"
    nfs_version: int = 3
    nfs_options: str = "soft,timeo=30,retrans=3"
    mount_retry_delay: int = 10
    mount_root_local: str = "/mnt/salt-and-soil"
    mount_root_remote: str = "/mnt/salt-and-soil"


@dataclass
class SyncConfig:
    scan_on_startup: bool = False
    auto_resume: bool = True
    compare_mode: CompareMode = CompareMode.SIZE_MTIME
    max_parallel_jobs: int = 2
    exclude_file: str = ""
    excludes: list[str] = field(default_factory=list)


@dataclass
class SourceConfig:
    """One logical sync target. Identified by `alias`; mounted under
    `(local_host, local_share)`; scan/sync happens at `mount_point + local_path`."""
    alias: str
    sort: int = 0
    agent: str = ""                # references AgentConfig.name (required on orchestrator)
    local_host: str = ""
    local_share: str = ""
    local_path: str = ""           # subdir under the share; "" = scan share root
    remote_share: str = ""
    remote_path: str = ""


@dataclass
class StateConfig:
    backend: str = "json"
    state_file: str = "./data/state/state.json"
    snapshot_dir: str = "./data/state/snapshots"


@dataclass
class AgentConfig:
    """Remote agent connection info (used by orchestrator).
    Per-source mount paths live in [[sources]], not here."""
    name: str = "agent-01"
    host: str = ""
    port: int = 8081
    api_key: str = ""
    ssh_host: str = ""
    ssh_user: str = "root"
    ssh_key_file: str = "/root/.ssh/saltsoil_key"


@dataclass
class AuthConfig:
    """Agent-side: expected X-Api-Key for incoming requests.
    Empty string means no auth enforced (matches legacy behaviour)."""
    api_key: str = ""


@dataclass
class Config:
    app: AppConfig
    server: ServerConfig
    mount_defaults: MountDefaults
    sync: SyncConfig
    state: StateConfig
    sources: list[SourceConfig] = field(default_factory=list)
    auth: AuthConfig = field(default_factory=AuthConfig)
    agents: list[AgentConfig] = field(default_factory=list)

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
class MountConfig:
    enabled: bool = True
    type: str = "nfs"
    remote_host: str = ""
    remote_share: str = ""
    local_mount_path: str = "/mnt/salt-and-soil/source"
    nfs_version: int = 3
    nfs_options: str = "soft,timeo=30,retrans=3"
    mount_retry_delay: int = 10


@dataclass
class SyncConfig:
    scan_on_startup: bool = False
    auto_resume: bool = True
    compare_mode: CompareMode = CompareMode.SIZE_MTIME
    max_parallel_jobs: int = 2
    sync_roots: list[str] = field(default_factory=lambda: ["videos"])
    exclude_file: str = ""
    excludes: list[str] = field(default_factory=list)


@dataclass
class StateConfig:
    backend: str = "json"
    state_file: str = "./data/state/state.json"
    snapshot_dir: str = "./data/state/snapshots"


@dataclass
class AgentConfig:
    """Remote agent connection info (used by orchestrator)."""
    name: str = "agent-01"
    host: str = ""
    port: int = 8081
    api_key: str = ""
    ssh_host: str = ""
    ssh_user: str = "root"
    ssh_key_file: str = "/root/.ssh/saltsoil_key"
    remote_mount_path: str = "/mnt/salt-and-soil/source"
    remote_share: str = ""


@dataclass
class AuthConfig:
    """Agent-side: expected X-Api-Key for incoming requests.
    Empty string means no auth enforced (matches legacy behaviour)."""
    api_key: str = ""


@dataclass
class Config:
    app: AppConfig
    server: ServerConfig
    mount: MountConfig
    sync: SyncConfig
    state: StateConfig
    auth: AuthConfig = field(default_factory=AuthConfig)
    agents: list[AgentConfig] = field(default_factory=list)

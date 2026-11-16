"""
Main entry point for Salt & Soil.
Loads config → determines role → starts FastAPI app.
"""
from __future__ import annotations

import logging
import stat
from pathlib import Path

from .config import load as load_config
from .config.loader import DEFAULT_CONFIG_PATH
from .config.models import Config
from .shared.enums import NodeRole
from .shared.paths import ensure_dir


def build_fastapi_app(config_path: str | None = None):
    """
    Builds and returns the FastAPI application.
    Can be used by uvicorn or by the test suite.
    """
    cfg = load_config(config_path)
    _setup_logging(cfg)
    _ensure_data_dirs(cfg)

    warning = config_exposure_warning(config_path or DEFAULT_CONFIG_PATH, cfg)
    if warning:
        logging.getLogger("salt-and-soil").warning(warning)

    if cfg.app.role == NodeRole.ORCHESTRATOR:
        from .roles.orchestrator import OrchestratorRuntime
        from .transport.api_server import create_app
        runtime = OrchestratorRuntime(cfg)
        return create_app(cfg, runtime)
    else:
        from .roles.agent import AgentRuntime
        from .transport.api_server import create_app
        runtime = AgentRuntime(cfg)
        return create_app(cfg, runtime)


def _setup_logging(cfg: Config) -> None:
    level = getattr(logging, cfg.app.log_level.upper(), logging.INFO)
    # Configure our named logger directly so uvicorn's dictConfig doesn't reset it
    logger = logging.getLogger("salt-and-soil")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(handler)
    logger.propagate = False


def _ensure_data_dirs(cfg: Config):
    base = Path(cfg.app.data_dir)
    # A data dir we create ourselves is owner-only: it holds auth.toml, state
    # and snapshots. An existing one keeps its mode — it may be shared on purpose.
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    for sub in ("state/snapshots", "cache", "logs", "exports"):
        ensure_dir(base / sub)


def config_exposure_warning(config_path: str | Path, cfg: Config) -> str | None:
    """A message if the config file holds an API key but other local users can
    read it; None otherwise. The file is the operator's, so we only warn."""
    if not (cfg.auth.api_key or any(a.api_key for a in cfg.agents)):
        return None
    try:
        mode = stat.S_IMODE(Path(config_path).stat().st_mode)
    except OSError:
        return None
    if not mode & 0o077:
        return None
    return (
        f"{config_path} contains an API key but is readable by other users "
        f"(mode {mode:04o}) — run: chmod 600 {config_path}"
    )

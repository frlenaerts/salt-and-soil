"""
Centrale entry point van Salt & Soil.
Laadt config → bepaalt rol → start FastAPI app.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .config import load as load_config
from .config.models import Config
from .shared.enums import NodeRole
from .shared.paths import ensure_dir


def build_fastapi_app(config_path: str | None = None):
    """
    Bouwt en geeft de FastAPI applicatie terug.
    Kan gebruikt worden door uvicorn of door de test suite.
    """
    cfg = load_config(config_path)
    _setup_logging(cfg)
    _ensure_data_dirs(cfg)

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
    for sub in ("state/snapshots", "cache", "logs", "exports"):
        ensure_dir(base / sub)

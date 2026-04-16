"""
Startup helper — gebruikt door cli.py en Dockerfile CMD.
"""
from __future__ import annotations
import uvicorn
from ..app import build_fastapi_app
from ..config import load as load_config


def serve(config_path: str | None = None):
    cfg = load_config(config_path)
    app = build_fastapi_app(config_path)
    uvicorn.run(
        app,
        host      = cfg.server.host,
        port      = cfg.server.port,
        log_level = cfg.app.log_level.lower(),
    )

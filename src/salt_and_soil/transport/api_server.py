"""
api_server.py

Single FastAPI application — role determines which routes are active:
  orchestrator → web UI + scan/execute API
  agent        → /mount /unmount /list /status /health
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..config.models import Config
from ..shared.enums import AppStatus, NodeRole
from ..shared.paths import human_size
from .dtos import ExecuteRequest, MountResponse, StatusResponse, ListDirsResponse

log = logging.getLogger("salt-and-soil")

_TMPL_DIR = Path(__file__).parent.parent / "templates"
_running  = True   # set to False in lifespan shutdown so SSE generators exit


def create_app(cfg: Config, runtime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        global _running
        _running = True
        yield
        _running = False   # SSE generators exit within 0.4 s

    app = FastAPI(title="Salt & Soil", lifespan=lifespan)

    if cfg.app.role == NodeRole.ORCHESTRATOR:
        _register_orchestrator_routes(app, cfg, runtime)
    else:
        _register_agent_routes(app, cfg, runtime)

    return app


# ══════════════════════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

def _register_orchestrator_routes(app: FastAPI, cfg: Config, rt):
    templates = Jinja2Templates(directory=str(_TMPL_DIR))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {
            "request":    request,
            "node_name":  cfg.app.node_name,
            "sync_roots": cfg.sync.sync_roots,
        })

    @app.post("/api/start")
    async def start(background_tasks: BackgroundTasks):
        if rt.status not in (AppStatus.IDLE, AppStatus.DONE, AppStatus.ERROR):
            raise HTTPException(400, "Busy")
        rt.reset()
        background_tasks.add_task(rt.run_scan)
        return {"ok": True}

    @app.get("/api/state")
    async def get_state():
        return rt.snapshot_for_ui()

    @app.get("/api/stream")
    async def stream():
        async def gen():
            sent_log    = 0
            sent_status = None
            while _running:
                snap = rt.snapshot_for_ui()
                cur_len = len(snap["log"])
                if snap["status"] != sent_status or cur_len != sent_log:
                    payload = {
                        "status":  snap["status"],
                        "new_log": snap["log"][sent_log:],
                        "diffs":   snap["diffs"] if snap["status"] in ("ready", "syncing", "done") else [],
                        "mount":   snap.get("mount"),
                        "error":   snap.get("error"),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    sent_log    = cur_len
                    sent_status = snap["status"]
                await asyncio.sleep(0.4)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/execute")
    async def execute(request: Request, background_tasks: BackgroundTasks):
        if rt.status != AppStatus.READY:
            raise HTTPException(400, "Scan first")
        body = await request.json()
        req  = ExecuteRequest.from_dict(body)
        rt.status = AppStatus.SYNCING
        background_tasks.add_task(rt.run_sync, req.actions)
        return {"ok": True}

    @app.post("/api/reset")
    async def reset():
        await rt.do_unmount()
        rt.reset()
        return {"ok": True}

    @app.get("/api/snapshots")
    async def list_snapshots():
        return rt.repo.list_snapshots()


# ══════════════════════════════════════════════════════════════════════════════
#  AGENT
# ══════════════════════════════════════════════════════════════════════════════

def _register_agent_routes(app: FastAPI, cfg: Config, rt):

    @app.post("/mount")
    async def mount():
        info = await rt.nfs.mount()
        return JSONResponse(MountResponse(
            ok      = info.is_ok,
            mounted = info.status.value == "mounted",
            msg     = "Mounted" if info.is_ok else "",
            error   = info.error,
        ).to_dict())

    @app.post("/unmount")
    async def unmount():
        ok = await rt.nfs.unmount()
        return JSONResponse(MountResponse(
            ok=ok, mounted=False,
            msg="Unmounted" if ok else "Error",
        ).to_dict())

    @app.get("/status")
    async def status():
        info = await rt.nfs.info()
        return JSONResponse(StatusResponse(
            ok          = True,
            node_name   = cfg.app.node_name,
            mounted     = info.status.value == "mounted",
            mount_point = cfg.mount.local_mount_path,
            nas_host    = cfg.mount.remote_host,
            total_bytes = info.total_bytes,
            free_bytes  = info.free_bytes,
            error       = info.error,
        ).to_dict())

    @app.get("/list")
    async def list_dirs(root: str = "videos"):
        if root not in cfg.sync.sync_roots:
            raise HTTPException(400, f"Sync root '{root}' not allowed")
        dirs = await rt.scan_root(root)
        return JSONResponse(ListDirsResponse(sync_root=root, dirs=dirs).to_dict())

    @app.get("/health")
    async def health():
        return {"ok": True, "node": cfg.app.node_name}

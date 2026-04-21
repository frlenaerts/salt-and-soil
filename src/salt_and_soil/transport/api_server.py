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
import signal
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, BackgroundTasks, Request, HTTPException, Depends, Header, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

import math

from ..auth import (
    AuthStore, LoginThrottle, hash_password, verify_password,
    make_session_token, verify_session_token,
)
from ..auth.password import MIN_PASSWORD_LENGTH
from ..auth.session import SESSION_COOKIE, REMEMBER_SECONDS, SESSION_SECONDS
from ..config.models import Config
from ..schedule.models import Schedule
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

        # Install signal handler NOW (after uvicorn's capture_signals) so
        # _running = False fires immediately on Ctrl+C, closing SSE generators
        # before uvicorn starts waiting for connections.
        loop = asyncio.get_running_loop()

        import os

        def _on_signal():
            global _running
            _running = False
            try:
                loop.remove_signal_handler(signal.SIGINT)
                loop.remove_signal_handler(signal.SIGTERM)
            except Exception:
                pass
            # Schedule the re-send so it fires after this handler returns,
            # avoiding KeyboardInterrupt being raised inside the signal callback.
            loop.call_soon(os.kill, os.getpid(), signal.SIGINT)

        try:
            loop.add_signal_handler(signal.SIGINT,  _on_signal)
            loop.add_signal_handler(signal.SIGTERM, _on_signal)
        except (NotImplementedError, RuntimeError):
            pass  # Windows / edge cases

        if hasattr(runtime, "start_schedule_loop"):
            await runtime.start_schedule_loop()

        try:
            yield
        finally:
            _running = False
            if hasattr(runtime, "stop_schedule_loop"):
                await runtime.stop_schedule_loop()

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

    auth_path     = Path(cfg.app.data_dir) / "auth.toml"
    auth_store    = AuthStore(auth_path)
    login_throttle = LoginThrottle()

    PUBLIC_PATHS = {"/login", "/logout", "/setup"}

    def _is_public(path: str) -> bool:
        return path in PUBLIC_PATHS

    def _authenticated_user(request: Request) -> str | None:
        if not auth_store.exists():
            return None
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        try:
            user = auth_store.load()
        except Exception:
            return None
        # Check long TTL first (remember-me); fall back to short TTL.
        uname = verify_session_token(user.session_secret, token, REMEMBER_SECONDS)
        if uname and uname == user.username:
            return uname
        return None

    # Pure-ASGI middleware (not BaseHTTPMiddleware) — the latter wraps streaming
    # responses in a task group that crashes on shutdown, spamming the log with
    # CancelledError tracebacks when the /api/stream SSE connection is torn down.
    class _AuthMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            path = scope["path"]
            if _is_public(path):
                await self.app(scope, receive, send)
                return
            request = Request(scope)
            if _authenticated_user(request):
                await self.app(scope, receive, send)
                return
            if not auth_store.exists():
                if path.startswith("/api/"):
                    resp = JSONResponse({"error": "setup required"}, status_code=401)
                else:
                    resp = RedirectResponse("/setup", status_code=303)
            elif path.startswith("/api/"):
                resp = JSONResponse({"error": "unauthorized"}, status_code=401)
            else:
                resp = RedirectResponse("/login", status_code=303)
            await resp(scope, receive, send)

    app.add_middleware(_AuthMiddleware)

    def _issue_session_cookie(response: Response, username: str, remember: bool) -> None:
        user = auth_store.load()
        token = make_session_token(user.session_secret, username)
        max_age = REMEMBER_SECONDS if remember else None
        response.set_cookie(
            key      = SESSION_COOKIE,
            value    = token,
            max_age  = max_age,
            httponly = True,
            samesite = "lax",
            secure   = False,
            path     = "/",
        )

    # ── Setup (first-run) ────────────────────────────────────────────────────
    @app.get("/setup", response_class=HTMLResponse)
    async def setup_get(request: Request):
        if auth_store.exists():
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse("setup.html", {
            "request": request, "title": "Setup", "error": None, "username": "",
        })

    @app.post("/setup", response_class=HTMLResponse)
    async def setup_post(
        request: Request,
        username: str  = Form(...),
        password: str  = Form(...),
        password2: str = Form(...),
    ):
        if auth_store.exists():
            return RedirectResponse("/login", status_code=303)

        uname = username.strip()
        err: str | None = None
        if not uname:
            err = "Username is required."
        elif len(password) < MIN_PASSWORD_LENGTH:
            err = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        elif password != password2:
            err = "Passwords do not match."

        if err:
            return templates.TemplateResponse("setup.html", {
                "request": request, "title": "Setup", "error": err, "username": uname,
            }, status_code=400)

        auth_store.create(uname, password)
        resp = RedirectResponse("/", status_code=303)
        _issue_session_cookie(resp, uname, remember=False)
        return resp

    # ── Login / Logout ───────────────────────────────────────────────────────
    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        if not auth_store.exists():
            return RedirectResponse("/setup", status_code=303)
        if _authenticated_user(request):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse("login.html", {
            "request": request, "title": "Sign in", "error": None, "username": "",
        })

    def _lockout_error(seconds: float) -> str:
        mins = max(1, math.ceil(seconds / 60))
        unit = "minute" if mins == 1 else "minutes"
        return f"Too many failed attempts. Try again in {mins} {unit}."

    @app.post("/login", response_class=HTMLResponse)
    async def login_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        remember: str | None = Form(default=None),
    ):
        if not auth_store.exists():
            return RedirectResponse("/setup", status_code=303)

        uname = username.strip()

        remaining = login_throttle.seconds_remaining()
        if remaining > 0:
            return templates.TemplateResponse("login.html", {
                "request":  request, "title": "Sign in",
                "error":    _lockout_error(remaining),
                "username": uname,
            }, status_code=429)

        try:
            user = auth_store.load()
        except Exception:
            user = None

        ok = bool(user and uname == user.username and verify_password(password, user.password_hash))
        if not ok:
            lockout = login_throttle.record_failure()
            error   = _lockout_error(lockout) if lockout > 0 else "Invalid username or password."
            return templates.TemplateResponse("login.html", {
                "request":  request, "title": "Sign in",
                "error":    error,
                "username": uname,
            }, status_code=429 if lockout > 0 else 401)

        login_throttle.record_success()
        resp = RedirectResponse("/", status_code=303)
        _issue_session_cookie(resp, uname, remember=bool(remember))
        return resp

    @app.post("/logout")
    async def logout_post():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    @app.get("/logout")
    async def logout_get():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    # ── Settings ─────────────────────────────────────────────────────────────
    @app.get("/api/settings")
    async def settings_get():
        user = auth_store.load()
        return {"username": user.username, "created_at": user.created_at}

    @app.post("/api/settings/password")
    async def settings_change_password(request: Request):
        body = await request.json()
        current  = str(body.get("current_password", ""))
        new_pw   = str(body.get("new_password", ""))
        confirm  = str(body.get("confirm_password", ""))

        user = auth_store.load()
        if not verify_password(current, user.password_hash):
            raise HTTPException(400, "Current password is incorrect.")
        if len(new_pw) < MIN_PASSWORD_LENGTH:
            raise HTTPException(400, f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if new_pw != confirm:
            raise HTTPException(400, "New passwords do not match.")

        updated = auth_store.change_password(new_pw)
        # Existing cookies (including the one that made this request) are
        # invalidated by rotating session_secret — issue a fresh one so the
        # user stays logged in on the current browser.
        resp = JSONResponse({"ok": True})
        _issue_session_cookie(resp, updated.username, remember=False)
        return resp

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        agent     = cfg.agents[0] if cfg.agents else None
        agent_str = agent.name if agent else ""
        user      = auth_store.load()
        return templates.TemplateResponse("index.html", {
            "request":    request,
            "node_name":  cfg.app.node_name,
            "sync_roots": cfg.sync.sync_roots,
            "nas_source": f"{cfg.mount.remote_host}:{cfg.mount.remote_share}",
            "local_path": cfg.mount.local_mount_path,
            "agent_str":  agent_str,
            "username":   user.username,
        })

    @app.post("/api/start")
    async def start(background_tasks: BackgroundTasks):
        if rt.status not in (AppStatus.IDLE, AppStatus.READY, AppStatus.DONE, AppStatus.ERROR):
            raise HTTPException(400, "Busy")
        rt.reset()
        background_tasks.add_task(rt.run_scan)
        return {"ok": True}

    @app.get("/api/state")
    async def get_state():
        return rt.snapshot_for_ui()

    @app.get("/api/stream")
    async def stream(request: Request):
        async def gen():
            sent_total  = 0
            sent_status = None
            try:
                while _running:
                    if await request.is_disconnected():
                        break
                    snap = rt.snapshot_for_ui()
                    cur_total = snap.get("log_total", len(snap["log"]))
                    if snap["status"] != sent_status or cur_total != sent_total:
                        log_list  = snap["log"]
                        new_count = cur_total - sent_total
                        if new_count <= 0:
                            new_log = []
                        elif new_count >= len(log_list):
                            new_log = log_list
                        else:
                            new_log = log_list[-new_count:]
                        payload = {
                            "status":       snap["status"],
                            "new_log":      new_log,
                            "diffs":        snap["diffs"] if snap["status"] in ("ready", "syncing", "done") else [],
                            "mount":        snap.get("mount"),
                            "error":        snap.get("error"),
                            "last_scan_at": snap.get("last_scan_at"),
                            "schedule":     snap.get("schedule"),
                            "cancelled":    snap.get("cancelled", False),
                        }
                        yield f"data: {json.dumps(payload)}\n\n"
                        sent_total  = cur_total
                        sent_status = snap["status"]
                    await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                pass
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

    @app.post("/api/cancel")
    async def cancel():
        if rt.status != AppStatus.SYNCING:
            raise HTTPException(400, "No sync in progress")
        ok = await rt.request_cancel()
        return {"ok": ok}

    @app.post("/api/log/clear")
    async def clear_log():
        rt.clear_log()
        return {"ok": True}

    @app.get("/api/snapshots")
    async def list_snapshots():
        return rt.repo.list_snapshots()

    @app.get("/api/schedule")
    async def get_schedule():
        return rt.get_schedule().to_dict()

    @app.post("/api/schedule")
    async def post_schedule(request: Request):
        body = await request.json()
        try:
            enabled = bool(body.get("enabled", False))
            days    = sorted({int(d) for d in body.get("days", [])})
            hour    = int(body.get("hour", 0))
            minute  = int(body.get("minute", 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid schedule payload")
        if any(d < 0 or d > 6 for d in days):
            raise HTTPException(400, "days must be in 0..6")
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise HTTPException(400, "hour must be 0..23, minute must be 0..59")
        if enabled and not days:
            raise HTTPException(400, "Enable at least one weekday")
        rt.save_schedule(Schedule(enabled=enabled, days=days, hour=hour, minute=minute))
        return rt.get_schedule().to_dict()


# ══════════════════════════════════════════════════════════════════════════════
#  AGENT
# ══════════════════════════════════════════════════════════════════════════════

def _register_agent_routes(app: FastAPI, cfg: Config, rt):
    expected_key = cfg.auth.api_key
    if not expected_key:
        log.warning("Agent running WITHOUT api_key — /mount /unmount /list /status are unprotected")

    def require_api_key(x_api_key: str | None = Header(default=None)):
        """Reject request if X-Api-Key header is missing or doesn't match
        auth.api_key. Empty config value disables the check (legacy behaviour)."""
        if not expected_key:
            return
        if not x_api_key or x_api_key != expected_key:
            raise HTTPException(status_code=401, detail="Invalid or missing X-Api-Key")

    protected = [Depends(require_api_key)]

    @app.post("/mount", dependencies=protected)
    async def mount():
        info = await rt.nfs.mount()
        return JSONResponse(MountResponse(
            ok          = info.is_ok,
            mounted     = info.status.value == "mounted",
            msg         = "Mounted" if info.is_ok else "",
            error       = info.error,
            total_bytes = info.total_bytes,
            free_bytes  = info.free_bytes,
        ).to_dict())

    @app.post("/unmount", dependencies=protected)
    async def unmount():
        ok = await rt.nfs.unmount()
        return JSONResponse(MountResponse(
            ok=ok, mounted=False,
            msg="Unmounted" if ok else "Error",
        ).to_dict())

    @app.get("/status", dependencies=protected)
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

    @app.get("/list", dependencies=protected)
    async def list_dirs(root: str = "videos"):
        if root not in cfg.sync.sync_roots:
            raise HTTPException(400, f"Sync root '{root}' not allowed")
        dirs = await rt.scan_root(root)
        return JSONResponse(ListDirsResponse(sync_root=root, dirs=dirs).to_dict())

    @app.get("/health")
    async def health():
        return {"ok": True, "node": cfg.app.node_name}

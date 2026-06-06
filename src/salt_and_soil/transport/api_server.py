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
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, RedirectResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import math

from .. import __version__
from ..auth import (
    AuthStore, LoginThrottle, verify_password,
    make_session_token, verify_session_token,
    User, ALL_ALIASES, UserExistsError, LastAdminError,
)
from ..auth.password import MIN_PASSWORD_LENGTH
from ..auth.session import SESSION_COOKIE, REMEMBER_SECONDS, SESSION_SECONDS
from ..config.models import Config
from ..schedule.models import Schedule
from ..shared.enums import AppStatus, NodeRole
from ..shared.paths import human_size
from .dtos import ExecuteRequest, MountResponse, StatusResponse, ListDirsResponse

log = logging.getLogger("salt-and-soil")

_TMPL_DIR   = Path(__file__).parent.parent / "templates"
_STATIC_DIR = Path(__file__).parent.parent / "static"
_running    = True   # set to False in lifespan shutdown so SSE generators exit


def create_app(cfg: Config, runtime) -> FastAPI:
    # Suppress CancelledError tracebacks that uvicorn logs during shutdown —
    # these come from Starlette's StreamingResponse (SSE) and lifespan task
    # groups being cancelled by Ctrl+C, which is expected behavior, not an app
    # error. Filter activates once _running flips to False in the signal handler.
    class _ShutdownNoiseFilter(logging.Filter):
        def filter(self, record):
            if _running:
                return True
            # During shutdown the event loop cancels in-flight ASGI tasks
            # (SSE generators, lifespan receive()). These surface as
            # CancelledError/KeyboardInterrupt tracebacks that uvicorn logs
            # at ERROR level — not actual app errors, just shutdown noise.
            if record.levelno >= logging.ERROR:
                return False
            return True

    _noise_filter = _ShutdownNoiseFilter()
    for _name in ("uvicorn", "uvicorn.error", "uvicorn.asgi", "asyncio"):
        logging.getLogger(_name).addFilter(_noise_filter)

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

    data_dir       = Path(cfg.app.data_dir)
    auth_store     = AuthStore(data_dir / "users.toml", legacy_path=data_dir / "auth.toml")
    login_throttle = LoginThrottle()
    all_aliases    = [s.alias for s in cfg.sources]

    PUBLIC_PATHS = {"/login", "/logout", "/setup", "/favicon.ico"}

    def _is_public(path: str) -> bool:
        return path in PUBLIC_PATHS or path.startswith("/static/")

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(_STATIC_DIR / "favicon.ico")

    def _current_user(request: Request) -> User | None:
        if not auth_store.exists():
            return None
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        try:
            secret = auth_store.session_secret()
        except Exception:
            return None
        # Long TTL covers both remember-me and session cookies (the cookie's own
        # max_age enforces the shorter window for non-remembered logins).
        res = verify_session_token(secret, token, REMEMBER_SECONDS)
        if not res:
            return None
        uname, ver = res
        user = auth_store.get(uname)
        # Reject sessions whose password version is stale (password was changed).
        if not user or ver != user.pw_version:
            return None
        return user

    def _require_user(request: Request) -> User:
        user = _current_user(request)
        if not user:
            raise HTTPException(401, "unauthorized")
        return user

    def _require_admin(request: Request) -> User:
        user = _require_user(request)
        if not user.is_admin:
            raise HTTPException(403, "Admin access required")
        return user

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
            if _current_user(request):
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

    def _issue_session_cookie(response: Response, user: User, remember: bool) -> None:
        token = make_session_token(auth_store.session_secret(), user.username, user.pw_version)
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

        # First account is always the admin with access to every source.
        user = auth_store.create(uname, password, is_admin=True, allowed_aliases=[ALL_ALIASES])
        resp = RedirectResponse("/", status_code=303)
        _issue_session_cookie(resp, user, remember=False)
        return resp

    # ── Login / Logout ───────────────────────────────────────────────────────
    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        if not auth_store.exists():
            return RedirectResponse("/setup", status_code=303)
        if _current_user(request):
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

        user = auth_store.get(uname)
        ok   = bool(user and verify_password(password, user.password_hash))
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
        _issue_session_cookie(resp, user, remember=bool(remember))
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

    # ── Settings (self-service) ──────────────────────────────────────────────
    @app.get("/api/settings")
    async def settings_get(request: Request):
        user = _require_user(request)
        return {
            "username":        user.username,
            "created_at":      user.created_at,
            "is_admin":        user.is_admin,
            "allowed_aliases": list(user.allowed_aliases),
        }

    @app.post("/api/settings/password")
    async def settings_change_password(request: Request):
        user = _require_user(request)
        body = await request.json()
        current  = str(body.get("current_password", ""))
        new_pw   = str(body.get("new_password", ""))
        confirm  = str(body.get("confirm_password", ""))

        if not verify_password(current, user.password_hash):
            raise HTTPException(400, "Current password is incorrect.")
        if len(new_pw) < MIN_PASSWORD_LENGTH:
            raise HTTPException(400, f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if new_pw != confirm:
            raise HTTPException(400, "New passwords do not match.")

        updated = auth_store.set_password(user.username, new_pw)
        # Bumping pw_version invalidates this user's other sessions (including the
        # cookie that made this request) — reissue so this browser stays logged in.
        resp = JSONResponse({"ok": True})
        _issue_session_cookie(resp, updated, remember=False)
        return resp

    # ── User management (admin only) ──────────────────────────────────────────
    def _user_dto(u: User) -> dict:
        return {
            "username":        u.username,
            "is_admin":        u.is_admin,
            "allowed_aliases": list(u.allowed_aliases),
            "created_at":      u.created_at,
        }

    def _validate_aliases(aliases: list[str]) -> None:
        invalid = [a for a in aliases if a != ALL_ALIASES and a not in all_aliases]
        if invalid:
            raise HTTPException(400, f"Unknown source(s): {', '.join(invalid)}")

    @app.get("/api/users")
    async def list_users(request: Request):
        _require_admin(request)
        return {
            "users":       [_user_dto(u) for u in auth_store.list()],
            "all_aliases": all_aliases,
        }

    @app.post("/api/users")
    async def create_user(request: Request):
        _require_admin(request)
        body     = await request.json()
        uname    = str(body.get("username", "")).strip()
        pw       = str(body.get("password", ""))
        confirm  = str(body.get("confirm_password", ""))
        is_admin = bool(body.get("is_admin", False))
        aliases  = [str(a) for a in body.get("allowed_aliases", [])]

        if not uname:
            raise HTTPException(400, "Username is required.")
        if len(pw) < MIN_PASSWORD_LENGTH:
            raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if pw != confirm:
            raise HTTPException(400, "Passwords do not match.")
        _validate_aliases(aliases)
        try:
            user = auth_store.create(uname, pw, is_admin=is_admin, allowed_aliases=aliases)
        except UserExistsError:
            raise HTTPException(409, f"User '{uname}' already exists.")
        return _user_dto(user)

    @app.put("/api/users/{username}")
    async def update_user(username: str, request: Request):
        _require_admin(request)
        if not auth_store.get(username):
            raise HTTPException(404, "User not found.")
        body     = await request.json()
        is_admin = bool(body.get("is_admin", False))
        aliases  = [str(a) for a in body.get("allowed_aliases", [])]
        _validate_aliases(aliases)
        try:
            user = auth_store.set_rights(username, is_admin, aliases)
        except LastAdminError as e:
            raise HTTPException(400, str(e))
        return _user_dto(user)

    @app.post("/api/users/{username}/password")
    async def set_user_password(username: str, request: Request):
        admin = _require_admin(request)
        if not auth_store.get(username):
            raise HTTPException(404, "User not found.")
        body    = await request.json()
        new_pw  = str(body.get("new_password", ""))
        confirm = str(body.get("confirm_password", ""))
        if len(new_pw) < MIN_PASSWORD_LENGTH:
            raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if new_pw != confirm:
            raise HTTPException(400, "Passwords do not match.")
        updated = auth_store.set_password(username, new_pw)
        # If the admin reset their OWN password here, the bumped pw_version just
        # invalidated their current cookie — reissue it so they stay logged in.
        resp = JSONResponse({"ok": True})
        if updated.username == admin.username:
            _issue_session_cookie(resp, updated, remember=False)
        return resp

    @app.delete("/api/users/{username}")
    async def delete_user(username: str, request: Request):
        admin = _require_admin(request)
        if username == admin.username:
            raise HTTPException(400, "You cannot delete your own account.")
        if not auth_store.get(username):
            raise HTTPException(404, "User not found.")
        try:
            auth_store.delete(username)
        except LastAdminError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        user      = _require_user(request)
        agent_str = ", ".join(a.name for a in cfg.agents)
        visible   = [s for s in cfg.sources if user.can_access(s.alias)]
        return templates.TemplateResponse("index.html", {
            "request":   request,
            "version":   __version__,
            "node_name": cfg.app.node_name,
            "aliases":   [s.alias for s in visible],
            "sources":   [
                {
                    "alias":       s.alias,
                    "agent":       s.agent,
                    "local_host":  s.local_host,
                    "local_share": s.local_share,
                    "local_path":  s.local_path,
                }
                for s in visible
            ],
            "agent_str":   agent_str,
            "username":    user.username,
            "is_admin":    user.is_admin,
            "all_aliases": all_aliases,
        })

    def _busy_conflict(scan_first: bool) -> HTTPException:
        """Build the response for a rejected scan/sync claim. Names the user who
        holds the runtime when it's genuinely busy; falls back to 'scan first'
        for a sync attempted from a non-ready (but free) state."""
        if rt.busy_op:
            who = f" by {rt.busy_user}" if rt.busy_user else ""
            return HTTPException(409, f"A {rt.busy_op} is already in progress{who}.")
        return HTTPException(400, "Scan first." if scan_first else "The system is busy.")

    @app.post("/api/start")
    async def start(request: Request, background_tasks: BackgroundTasks):
        user = _require_user(request)
        # Atomic claim (no await before it) — closes the race where two users
        # could both pass a status check before either marked the runtime busy.
        if not rt.try_begin_scan(user.username):
            raise _busy_conflict(scan_first=False)
        rt.reset(set_idle=False)   # clear prior log/diffs but keep the busy claim
        aliases = None if user.has_all_access else list(user.allowed_aliases)
        background_tasks.add_task(rt.run_scan, aliases)
        return {"ok": True}

    @app.get("/api/state")
    async def get_state(request: Request):
        user = _require_user(request)
        snap = rt.snapshot_for_ui()
        if not user.has_all_access:
            snap = {**snap, "diffs": [d for d in snap["diffs"] if user.can_access(d["source_alias"])]}
        return snap

    @app.get("/api/stream")
    async def stream(request: Request):
        user = _require_user(request)
        async def gen():
            sent_total  = 0
            sent_status = None
            try:
                while _running:
                    if await request.is_disconnected():
                        break
                    snap = rt.snapshot_for_ui()
                    cur_total = snap.get("log_total", len(snap["log"]))
                    # Reset detection: when /api/start runs rt.reset(), log_total
                    # drops back to 0 but our sent_total still holds the previous
                    # run's tally. Without this, the first burst of log lines
                    # after a reset (typically the mount lines) gets dropped
                    # because the delta computation goes negative.
                    if cur_total < sent_total:
                        sent_total = 0
                    if snap["status"] != sent_status or cur_total != sent_total:
                        log_list  = snap["log"]
                        new_count = cur_total - sent_total
                        if new_count <= 0:
                            new_log = []
                        elif new_count >= len(log_list):
                            new_log = log_list
                        else:
                            new_log = log_list[-new_count:]
                        diffs_out = snap["diffs"] if snap["status"] in ("ready", "syncing", "done") else []
                        if diffs_out and not user.has_all_access:
                            diffs_out = [d for d in diffs_out if user.can_access(d["source_alias"])]
                        payload = {
                            "status":       snap["status"],
                            "new_log":      new_log,
                            "diffs":        diffs_out,
                            "mounts":       snap.get("mounts", []),
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
        user = _require_user(request)
        # Atomic claim first (succeeds only from READY) — no await precedes it,
        # so two concurrent executes can't both get past it.
        if not rt.try_begin_sync(user.username):
            raise _busy_conflict(scan_first=True)
        # From here we hold the claim; release it (abort_begin) on any rejection
        # so the runtime doesn't stay stuck in a busy state.
        try:
            body = await request.json()
            req  = ExecuteRequest.from_dict(body)
        except Exception:
            rt.abort_begin()
            raise HTTPException(400, "Invalid request body.")
        # Enforce scope: a user may only act on sources they have rights to.
        forbidden = sorted({a.source_alias for a in req.actions if not user.can_access(a.source_alias)})
        if forbidden:
            rt.abort_begin()
            raise HTTPException(403, f"No access to source(s): {', '.join(forbidden)}")
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

    def _require_alias(alias: str | None) -> str:
        if not alias:
            raise HTTPException(400, "Missing 'alias'")
        if alias not in rt.sources_by_alias:
            raise HTTPException(
                400,
                f"Unknown source alias '{alias}' "
                f"(known: {sorted(rt.sources_by_alias.keys())})",
            )
        return alias

    @app.post("/mount", dependencies=protected)
    async def mount(request: Request):
        body  = await request.json() if await _has_body(request) else {}
        alias = _require_alias(body.get("alias"))
        nfs   = rt.mount_for(alias)
        info  = await nfs.mount()
        return JSONResponse(MountResponse(
            ok          = info.is_ok,
            mounted     = info.status.value == "mounted",
            msg         = "Mounted" if info.is_ok else "",
            error       = info.error,
            total_bytes = info.total_bytes,
            free_bytes  = info.free_bytes,
        ).to_dict())

    @app.post("/unmount", dependencies=protected)
    async def unmount(request: Request):
        body  = await request.json() if await _has_body(request) else {}
        alias = body.get("alias")
        if alias:
            _require_alias(alias)
            ok = await rt.mount_for(alias).unmount()
            return JSONResponse(MountResponse(
                ok=ok, mounted=False,
                msg="Unmounted" if ok else "Error",
            ).to_dict())
        # No alias → unmount everything currently registered
        results = await asyncio.gather(*[m.unmount() for m in rt.registry.all()])
        ok = all(results) if results else True
        return JSONResponse(MountResponse(
            ok=ok, mounted=False,
            msg=f"Unmounted {sum(results)} of {len(results)}" if results else "No mounts",
        ).to_dict())

    @app.get("/status", dependencies=protected)
    async def status():
        mounts: list[dict] = []
        for alias, src in rt.sources_by_alias.items():
            nfs  = rt.mount_for(alias)
            info = await nfs.info()
            mounts.append({
                "alias":       alias,
                "host":        src.local_host,
                "share":       src.local_share,
                "mount_point": nfs.mount_point,
                "mounted":     info.status.value == "mounted",
                "total_bytes": info.total_bytes,
                "free_bytes":  info.free_bytes,
                "error":       info.error,
            })
        return JSONResponse(StatusResponse(
            ok        = True,
            node_name = cfg.app.node_name,
            mounts    = mounts,
        ).to_dict())

    @app.get("/list", dependencies=protected)
    async def list_dirs(alias: str | None = None):
        a = _require_alias(alias)
        dirs = await rt.list_alias(a)
        return JSONResponse(ListDirsResponse(source_alias=a, dirs=dirs).to_dict())

    @app.get("/health")
    async def health():
        return {"ok": True, "node": cfg.app.node_name}


async def _has_body(request: Request) -> bool:
    """True iff request has a non-empty body. Used to keep `POST /unmount` with
    no body working as 'unmount all'."""
    ctype = request.headers.get("content-type", "")
    if "json" not in ctype:
        return False
    body = await request.body()
    return len(body) > 0

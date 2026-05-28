#!/usr/bin/env python3
"""
test_scan.py

Starts a local Salt & Soil orchestrator in 'test mode':

  1. Load config (default config/config.toml)
  2. Mount each unique (host, share) in [[sources]] via NFS (no agent traffic)
  3. Scan every source one level deep
  4. Serve a read-only web UI at http://localhost:<port>
     -> shows all folders with sizes, diff status (local only, no agent)

Usage:
  python scripts/test_scan.py
  python scripts/test_scan.py --config config/orchestrator.toml
  python scripts/test_scan.py --port 9090
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import posixpath
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

from salt_and_soil.config import load as load_config
from salt_and_soil.mounts.registry import MountRegistry
from salt_and_soil.mounts.checks import assert_mount_ok, is_path_empty, MountCheckError
from salt_and_soil.scanner.scanner import DirScanner
from salt_and_soil.state.repository import StateRepository
from salt_and_soil.shared.enums import AppStatus
from salt_and_soil.shared.paths import human_size, ensure_dir

log = logging.getLogger("saltsoil.test")


# ── Global test state ──────────────────────────────────────────────────────────

class TestState:
    status:    AppStatus = AppStatus.IDLE
    _log:      list[str] = []
    _diffs:    list[dict] = []
    _mounts:   list[dict] = []
    _error:    str = ""
    _server:   uvicorn.Server | None = None
    _progress: float = 0.0
    _running:  bool = True

    @staticmethod
    def _ts() -> str:
        from datetime import datetime
        return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    def info(self, msg: str):
        log.info(msg)
        self._log.append(f"{self._ts()} - {msg}")

    def err(self, msg: str):
        log.error(msg)
        self._log.append(f"{self._ts()} - ⚠ {msg}")

    def reset(self):
        self._log      = []
        self._diffs    = []
        self._mounts   = []
        self._error    = ""
        self._progress = 0.0
        self.status    = AppStatus.IDLE

    def snapshot(self) -> dict:
        return {
            "status":   self.status.value,
            "log":      list(self._log),
            "diffs":    list(self._diffs),
            "mounts":   list(self._mounts),
            "error":    self._error,
            "progress": self._progress,
        }


ts = TestState()


# ── Shutdown helper ────────────────────────────────────────────────────────────

def _trigger_shutdown():
    """Called from signal handler or API — closes SSE streams, then stops server."""
    ts._running = False
    if ts._server:
        ts._server.should_exit = True


# ── FastAPI test app ───────────────────────────────────────────────────────────

def create_test_app(cfg, registry: MountRegistry, repo: StateRepository) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app):
        yield
        log.info("Shutdown: unmounting NFS...")
        for nfs in registry.all():
            try:
                await nfs.unmount()
            except Exception as e:
                log.warning("Unmount on shutdown failed: %s", e)
        log.info("Unmounted.")

    app = FastAPI(title="Salt & Soil — Test Scan", lifespan=lifespan)

    tmpl_path = Path(__file__).parent.parent / "src/salt_and_soil/templates/index.html"
    tmpl_html = tmpl_path.read_text() if tmpl_path.exists() else "<h1>Template not found</h1>"

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(tmpl_html)

    @app.post("/api/start")
    async def start():
        if ts.status in (AppStatus.MOUNTING, AppStatus.SCANNING):
            return {"error": "Busy"}
        ts.reset()
        asyncio.create_task(_do_scan(cfg, registry, repo))
        return {"ok": True}

    @app.get("/api/state")
    async def get_state():
        return ts.snapshot()

    @app.get("/api/stream")
    async def stream():
        async def gen():
            sent = 0; prev_status = None; prev_progress = -1.0
            while ts._running:
                snap = ts.snapshot()
                cur  = len(snap["log"])
                changed = (
                    snap["status"]   != prev_status   or
                    cur              != sent           or
                    snap["progress"] != prev_progress
                )
                if changed:
                    payload = {
                        "status":   snap["status"],
                        "new_log":  snap["log"][sent:],
                        "diffs":    snap["diffs"] if snap["status"] in ("ready", "done") else [],
                        "mounts":   snap["mounts"],
                        "error":    snap["error"],
                        "progress": snap["progress"],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    sent = cur; prev_status = snap["status"]; prev_progress = snap["progress"]
                await asyncio.sleep(0.4)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/reset")
    async def api_reset():
        for nfs in registry.all():
            try:
                await nfs.unmount()
            except Exception:
                pass
        ts.reset()
        return {"ok": True}

    @app.post("/api/stop")
    async def stop():
        ts.info("Unmounting...")
        for nfs in registry.all():
            try:
                await nfs.unmount()
            except Exception:
                pass
        ts.info("Stopping server...")
        _trigger_shutdown()
        return {"ok": True}

    @app.post("/api/execute")
    async def execute_disabled():
        return {"error": "Sync not available in test mode — no agent configured"}

    @app.get("/api/snapshots")
    async def snapshots():
        return repo.list_snapshots()

    return app


# ── Scan logic ────────────────────────────────────────────────────────────────

async def _do_scan(cfg, registry: MountRegistry, repo: StateRepository):
    ts.status    = AppStatus.MOUNTING
    ts._progress = 0.0
    try:
        # 1. Mount each unique (host, share) pair
        unique_pairs = sorted({(s.local_host, s.local_share) for s in cfg.sources})
        ts._mounts = []
        for host, share in unique_pairs:
            nfs  = registry.get_or_create(host, share)
            ts.info(f"Mounting {host}:{share} → {nfs.mount_point}")
            info = await nfs.mount()
            ts._mounts.append({
                "host":        host,
                "share":       share,
                "mount_point": nfs.mount_point,
                "status":      info.status.value,
                "writable":    info.writable,
                "total":       human_size(info.total_bytes),
                "free":        human_size(info.free_bytes),
            })
            assert_mount_ok(info)
            ts.info(f"Mounted — {human_size(info.total_bytes)} total, {human_size(info.free_bytes)} free")
            if is_path_empty(nfs.mount_point):
                raise MountCheckError(f"Mount path {nfs.mount_point} is empty — NFS share not reachable?")

        # 2. Scan each source
        ts.status    = AppStatus.SCANNING
        ts._progress = 5.0
        scanner = DirScanner(node_name=cfg.app.node_name, excludes=cfg.sync.excludes)
        ts.info(f"Scanning: {', '.join(s.alias for s in cfg.sources)}...")

        diffs = []
        n = len(cfg.sources)
        for i, src in enumerate(cfg.sources):
            mp        = registry.get_or_create(src.local_host, src.local_share).mount_point
            scan_path = posixpath.join(mp, src.local_path) if src.local_path else mp
            snap      = await scanner.scan_source(scan_path, src.alias)
            repo.save_snapshot(snap)
            ts._progress = 5.0 + ((i + 1) / n) * 93.0
            ts.info(f"  {src.alias}: {snap.entry_count} folders ({human_size(snap.total_size)})")
            for entry in snap.top_level_dirs():
                diffs.append({
                    "source_alias":   snap.source_alias,
                    "name":           entry.relative_path,
                    "diff_status":    "local_only",
                    "local_size":     entry.size,
                    "remote_size":    0,
                    "local_size_hr":  entry.size_hr(),
                    "remote_size_hr": "—",
                    "planned_action": "skip",
                })

        ts._diffs    = diffs
        ts._progress = 100.0
        ts.status    = AppStatus.READY
        ts.info(f"Done — {len(diffs)} folders found.")

    except Exception as e:
        ts._error = str(e)
        ts.err(str(e))
        ts.status    = AppStatus.ERROR
        ts._progress = 0.0


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_test(config_path: str | None = None, port: int | None = None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg         = load_config(config_path)
    listen_port = port or cfg.server.port

    ensure_dir(cfg.app.data_dir + "/state/snapshots")
    ensure_dir(cfg.app.data_dir + "/cache")
    ensure_dir(cfg.app.data_dir + "/logs")

    registry = MountRegistry(cfg.mount_defaults, side="local")
    repo     = StateRepository(
        state_file   = cfg.state.state_file,
        snapshot_dir = cfg.state.snapshot_dir,
    )

    fastapi_app = create_test_app(cfg, registry, repo)

    uvi_cfg = uvicorn.Config(
        fastapi_app,
        host                     = "0.0.0.0",
        port                     = listen_port,
        log_level                = "warning",
        timeout_graceful_shutdown = 2,
    )
    server     = uvicorn.Server(uvi_cfg)
    ts._server = server

    def _on_signal(sig, frame):
        ts._running = False
        server.should_exit = True

    signal.signal(signal.SIGINT,  _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    async def _auto_scan():
        await asyncio.sleep(0.8)
        await _do_scan(cfg, registry, repo)

    asyncio.create_task(_auto_scan())

    print(f"\n  Salt & Soil — Test Scan")
    print(f"  ─────────────────────────────────────────")
    print(f"  URL:     http://localhost:{listen_port}")
    print(f"  Sources: {', '.join(s.alias for s in cfg.sources)}")
    print(f"  Stop:    Ctrl+C\n")

    await server.serve()


def main():
    parser = argparse.ArgumentParser(description="Salt & Soil — test scan")
    parser.add_argument("--config", default=None)
    parser.add_argument("--port",   type=int, default=None)
    args = parser.parse_args()
    asyncio.run(run_test(args.config, args.port))


if __name__ == "__main__":
    main()

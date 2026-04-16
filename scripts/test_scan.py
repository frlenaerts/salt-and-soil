#!/usr/bin/env python3
"""
test_scan.py

Start een lokale Salt & Soil orchestrator in 'test-modus':

  1. Laad config (standaard config/config.toml)
  2. Mount de lokale NAS via NFS
  3. Scan alle sync_roots
  4. Bouw een read-only web UI op http://localhost:<port>
     → toont alle mappen met groottes, diff-status (lokaal only, want geen agent)
  5. Knop "Unmount & Stop" in de UI ontkoppelt de NAS en stopt de server

Gebruik:
  python scripts/test_scan.py
  python scripts/test_scan.py --config config/orchestrator.toml
  python scripts/test_scan.py --port 9090
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

# Zorg dat de src/ map in het pad zit
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from salt_and_soil.config import load as load_config
from salt_and_soil.mounts.nfs import NFSMount
from salt_and_soil.mounts.checks import assert_mount_ok, is_path_empty, MountCheckError
from salt_and_soil.scanner.scanner import DirScanner
from salt_and_soil.state.repository import StateRepository
from salt_and_soil.shared.enums import AppStatus
from salt_and_soil.shared.paths import human_size, ensure_dir
from salt_and_soil.shared.clock import utc_now_iso

log = logging.getLogger("saltsoil.test")

# ── Global test state ──────────────────────────────────────────────────────────

class TestState:
    status: AppStatus = AppStatus.IDLE
    _log:   list[str] = []
    _diffs: list[dict] = []
    _mount: dict | None = None
    _error: str = ""
    _server: uvicorn.Server | None = None

    def info(self, msg: str):
        log.info(msg)
        self._log.append(msg)

    def err(self, msg: str):
        log.error(msg)
        self._log.append(f"⚠ {msg}")

    def snapshot(self) -> dict:
        return {
            "status": self.status.value,
            "log":    list(self._log),
            "diffs":  list(self._diffs),
            "mount":  self._mount,
            "error":  self._error,
        }

ts = TestState()


# ── FastAPI test app ───────────────────────────────────────────────────────────

def create_test_app(cfg, nfs: NFSMount, repo: StateRepository) -> FastAPI:
    app = FastAPI(title="Salt & Soil — Test Scan")

    # Load the shared template
    tmpl_path = Path(__file__).parent.parent / "src/salt_and_soil/templates/index.html"
    tmpl_html = tmpl_path.read_text() if tmpl_path.exists() else "<h1>Template niet gevonden</h1>"

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(tmpl_html)

    @app.post("/api/start")
    async def start():
        """In test modus: herstart de scan."""
        if ts.status in (AppStatus.MOUNTING, AppStatus.SCANNING):
            return {"error": "Bezig"}
        ts._log = []; ts._diffs = []; ts._error = ""; ts._mount = None
        asyncio.create_task(_do_scan(cfg, nfs, repo))
        return {"ok": True}

    @app.get("/api/state")
    async def get_state():
        return ts.snapshot()

    @app.get("/api/stream")
    async def stream():
        async def gen():
            sent = 0; prev_status = None
            while True:
                snap = ts.snapshot()
                cur  = len(snap["log"])
                if snap["status"] != prev_status or cur != sent:
                    yield f"data: {json.dumps({'status': snap['status'], 'new_log': snap['log'][sent:], 'diffs': snap['diffs'] if snap['status'] in ('ready','done') else [], 'mount': snap['mount'], 'error': snap['error']})}\n\n"
                    sent = cur; prev_status = snap["status"]
                await asyncio.sleep(0.4)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/reset")
    async def reset():
        """Herstart: unmount + reset state."""
        await nfs.unmount()
        ts.status = AppStatus.IDLE
        ts._log   = []; ts._diffs = []; ts._mount = None; ts._error = ""
        return {"ok": True}

    @app.post("/api/stop")
    async def stop():
        """Unmount en stop de server."""
        ts.info("Unmounten...")
        await nfs.unmount()
        ts.info("Server stopt over 1 seconde.")
        asyncio.create_task(_delayed_stop())
        return {"ok": True}

    # Disable sync execute in test mode (no agent)
    @app.post("/api/execute")
    async def execute_disabled():
        return {"error": "Sync niet beschikbaar in test-modus (geen agent geconfigureerd)"}

    @app.get("/api/snapshots")
    async def snapshots():
        return repo.list_snapshots()

    return app


async def _delayed_stop():
    await asyncio.sleep(1.2)
    if ts._server:
        ts._server.should_exit = True


async def _do_scan(cfg, nfs: NFSMount, repo: StateRepository):
    ts.status = AppStatus.MOUNTING
    try:
        # 1. Mount
        ts.info(f"NAS mounten: {nfs.host}:{nfs.share} → {nfs.mount_point}")
        info = await nfs.mount()
        ts._mount = {
            "source":     info.source,
            "local_path": info.local_path,
            "status":     info.status.value,
            "writable":   info.writable,
            "total":      human_size(info.total_bytes),
            "free":       human_size(info.free_bytes),
        }
        assert_mount_ok(info)
        ts.info(f"✓ Gemount — {human_size(info.total_bytes)} totaal, {human_size(info.free_bytes)} vrij")

        if is_path_empty(cfg.mount.local_mount_path):
            raise MountCheckError("Mount pad is leeg — NFS share niet bereikbaar?")

        # 2. Scan
        ts.status = AppStatus.SCANNING
        ts.info(f"Scannen: {', '.join(cfg.sync.sync_roots)}...")
        scanner = DirScanner(
            mount_point = cfg.mount.local_mount_path,
            sync_roots  = cfg.sync.sync_roots,
            node_name   = cfg.app.node_name,
        )
        diffs = []
        for snap in await scanner.scan_all():
            repo.save_snapshot(snap)
            ts.info(f"  /{snap.sync_root}: {snap.entry_count} mappen gevonden ({human_size(snap.total_size)})")
            for entry in snap.top_level_dirs():
                diffs.append({
                    "sync_root":      snap.sync_root,
                    "name":           entry.relative_path,
                    "diff_status":    "local_only",   # geen agent in test modus
                    "local_size":     entry.size,
                    "remote_size":    0,
                    "local_size_hr":  entry.size_hr(),
                    "remote_size_hr": "—",
                    "planned_action": "skip",
                })

        ts._diffs = diffs
        ts.status = AppStatus.READY
        ts.info(f"✓ Klaar — {len(diffs)} mappen gevonden. Klik 'Unmount & Stop' als je klaar bent.")

    except (MountCheckError, Exception) as e:
        ts._error = str(e)
        ts.err(str(e))
        ts.status = AppStatus.ERROR


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_test(config_path: str | None = None, port: int | None = None):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(config_path)

    # Override port indien opgegeven
    listen_port = port or cfg.server.port

    # Zorg voor data dirs
    ensure_dir(cfg.app.data_dir + "/state/snapshots")
    ensure_dir(cfg.app.data_dir + "/cache")
    ensure_dir(cfg.app.data_dir + "/logs")

    nfs = NFSMount(
        host        = cfg.mount.remote_host,
        share       = cfg.mount.remote_share,
        mount_point = cfg.mount.local_mount_path,
        nfs_version = cfg.mount.nfs_version,
        nfs_options = cfg.mount.nfs_options,
    )
    repo = StateRepository(
        state_file   = cfg.state.state_file,
        snapshot_dir = cfg.state.snapshot_dir,
    )

    fastapi_app = create_test_app(cfg, nfs, repo)

    # Start scan automatisch bij opstart
    log.info(f"Salt & Soil test-scan start op http://localhost:{listen_port}")
    log.info(f"Config: {cfg.app.node_name} ({cfg.app.role.value})")
    log.info(f"NAS: {cfg.mount.remote_host}:{cfg.mount.remote_share} → {cfg.mount.local_mount_path}")

    uvi_cfg = uvicorn.Config(
        fastapi_app,
        host      = "0.0.0.0",
        port      = listen_port,
        log_level = "warning",   # uvicorn zelf stil houden
    )
    server = uvicorn.Server(uvi_cfg)
    ts._server = server

    # Start scan na korte delay zodat de server tijd heeft op te starten
    async def _auto_scan():
        await asyncio.sleep(0.8)
        await _do_scan(cfg, nfs, repo)

    asyncio.create_task(_auto_scan())

    print(f"\n  Salt & Soil — Test Scan")
    print(f"  ─────────────────────────────────────────")
    print(f"  URL:   http://localhost:{listen_port}")
    print(f"  NAS:   {cfg.mount.remote_host}:{cfg.mount.remote_share}")
    print(f"  Roots: {', '.join(cfg.sync.sync_roots)}")
    print(f"  Stop:  Ctrl+C  of  POST /api/stop\n")

    await server.serve()


def main():
    parser = argparse.ArgumentParser(description="Salt & Soil — test scan")
    parser.add_argument("--config", default=None)
    parser.add_argument("--port",   type=int, default=None)
    args = parser.parse_args()
    asyncio.run(run_test(args.config, args.port))


if __name__ == "__main__":
    main()

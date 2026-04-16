"""
CLI voor Salt & Soil.

  python -m salt_and_soil serve          # start web server
  python -m salt_and_soil serve --config ./config/agent.toml
  python -m salt_and_soil scan           # scan zonder UI (debug)
  python -m salt_and_soil test-mount     # mount, scan, toon UI, unmount na stop
"""
from __future__ import annotations

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(prog="salt-and-soil")
    parser.add_argument("--config", default=None, help="Pad naar config TOML bestand")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve",      help="Start de web server")
    sub.add_parser("test-mount", help="Mount, scan, toon UI, unmount na stop")

    scan_p = sub.add_parser("scan", help="Scan en dump resultaat (geen UI)")
    scan_p.add_argument("--root", default=None)

    args = parser.parse_args()

    if args.config:
        os.environ["SALTSOIL_CONFIG"] = args.config

    if args.command == "serve" or args.command is None:
        _cmd_serve()
    elif args.command == "test-mount":
        _cmd_test_mount()
    elif args.command == "scan":
        _cmd_scan(args.root)
    else:
        parser.print_help()


def _cmd_serve():
    import uvicorn
    from .app import build_fastapi_app
    from .config import load as load_config
    cfg     = load_config()
    fastapi = build_fastapi_app()
    uvicorn.run(fastapi, host=cfg.server.host, port=cfg.server.port, log_level="info")


def _cmd_test_mount():
    """Start de test-mount flow — zie scripts/test_scan.py voor details."""
    import asyncio
    from scripts.test_scan import run_test
    asyncio.run(run_test())


def _cmd_scan(root: str | None):
    import asyncio
    from .config import load as load_config
    from .scanner.scanner import DirScanner
    from .shared.paths import human_size

    async def _scan():
        cfg     = load_config()
        roots   = [root] if root else cfg.sync.sync_roots
        scanner = DirScanner(cfg.mount.local_mount_path, roots, cfg.app.node_name)
        for snap in await scanner.scan_all():
            print(f"\n/{snap.sync_root}  ({snap.entry_count} mappen, {human_size(snap.total_size)})")
            for e in snap.top_level_dirs():
                print(f"  {e.relative_path:<40} {e.size_hr()}")

    asyncio.run(_scan())


if __name__ == "__main__":
    main()

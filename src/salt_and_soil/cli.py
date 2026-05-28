"""
CLI for Salt & Soil.

  python -m salt_and_soil serve          # start web server
  python -m salt_and_soil serve --config ./config/agent.toml
  python -m salt_and_soil scan           # scan without UI (debug)
  python -m salt_and_soil test-mount     # mount, scan, show UI, unmount on stop
"""
from __future__ import annotations

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(prog="salt-and-soil")
    parser.add_argument("--config", default=None, help="Path to config TOML file")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve",      help="Start the web server")
    sub.add_parser("test-mount", help="Mount, scan, show UI, unmount on stop")

    scan_p = sub.add_parser("scan", help="Scan and dump result (no UI)")
    scan_p.add_argument("--alias", default=None,
                        help="Only scan the source with this alias (default: all)")

    args = parser.parse_args()

    if args.config:
        os.environ["SALTSOIL_CONFIG"] = args.config

    if args.command == "serve" or args.command is None:
        _cmd_serve()
    elif args.command == "test-mount":
        _cmd_test_mount()
    elif args.command == "scan":
        _cmd_scan(args.alias)
    else:
        parser.print_help()


def _cmd_serve():
    import uvicorn
    from .app import build_fastapi_app
    from .config import load as load_config
    cfg     = load_config()
    fastapi = build_fastapi_app()
    uvicorn.run(fastapi, host=cfg.server.host, port=cfg.server.port,
                log_level=cfg.app.log_level.lower(),
                timeout_graceful_shutdown=3)


def _cmd_test_mount():
    """Start the test-mount flow — see scripts/test_scan.py for details."""
    import asyncio
    from scripts.test_scan import run_test
    asyncio.run(run_test())


def _cmd_scan(alias: str | None):
    import asyncio
    import posixpath
    from .config import load as load_config
    from .mounts.registry import MountRegistry
    from .scanner.scanner import DirScanner
    from .shared.paths import human_size

    async def _scan():
        cfg      = load_config()
        registry = MountRegistry(cfg.mount_defaults, side="local")
        scanner  = DirScanner(cfg.app.node_name, cfg.sync.excludes)

        sources = [s for s in cfg.sources if alias is None or s.alias == alias]
        if alias and not sources:
            print(f"Unknown alias '{alias}'. Known: {[s.alias for s in cfg.sources]}")
            return

        for src in sources:
            nfs       = registry.get_or_create(src.local_host, src.local_share)
            scan_path = posixpath.join(nfs.mount_point, src.local_path) if src.local_path else nfs.mount_point
            snap      = await scanner.scan_source(scan_path, src.alias)
            print(f"\n{src.alias}  ({snap.entry_count} folders, {human_size(snap.total_size)})")
            for e in snap.top_level_dirs():
                print(f"  {e.relative_path:<40} {e.size_hr()}")

    asyncio.run(_scan())


if __name__ == "__main__":
    main()

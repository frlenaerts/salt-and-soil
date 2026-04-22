# Architecture

This document provides developer-oriented guidance for working with the Salt & Soil codebase.

## Git Commits

Do not add automated co-author lines from tooling in commit messages.

## Project Overview

**Salt & Soil** is a dual-node directory synchronization tool. It syncs selected directories between two storage systems using on-demand NFS mounts and rsync over SSH, designed to work while the storage devices are normally in sleep mode.

## Commands

```bash
# Run server (reads ./config/config.toml or $SALTSOIL_CONFIG)
python -m salt_and_soil serve
python -m salt_and_soil serve --config ./config/agent.toml

# Scan without UI (debug)
python -m salt_and_soil scan
python -m salt_and_soil scan --root videos

# Mount + scan + UI test mode (unmounts on stop)
python -m salt_and_soil test-mount

# Tests
pytest
pytest -v
pytest tests/test_comparer.py
pytest -k test_name
```

## Architecture

The system has two runtime roles configured via `config.toml`:

**Orchestrator** (`app.role = "orchestrator"`) — runs on the initiating machine:
1. Mounts local storage via NFS
2. Calls agent API to mount remote storage
3. `DirScanner` scans both local and remote directories (via agent `/list` endpoint)
4. `Comparer` diffs snapshots (size + mtime, 1% tolerance)
5. Presents HTML UI with diffs; user selects sync actions
6. `sync.planner.build_jobs` turns the selected diffs into `SyncJob`s
7. `SyncExecutor` runs `rsync -avz` over SSH for each job
8. Unmounts both sides

**Agent** (`app.role = "agent"`) — runs on the remote machine, exposes a REST API:
- `POST /mount` / `POST /unmount` — NFS mount lifecycle
- `GET /list?root=videos` — directory listing for comparison
- `GET /status`, `GET /health`

### Entry Points

- `src/salt_and_soil/__main__.py` → `cli.py` → `app.py:build_fastapi_app()`
- `app.py` reads config, detects role, instantiates `OrchestratorRuntime` or `AgentRuntime`, creates FastAPI app with role-specific routes

### Key Module Map

| Module | Purpose |
|--------|---------|
| `config/` | TOML config loading into typed dataclasses |
| `roles/orchestrator.py` | Full sync lifecycle: scan → compare → sync |
| `roles/agent.py` | Remote mount + scan endpoint |
| `scanner/scanner.py` | Recursive dir scan using `du -sb` + stat |
| `sync/comparer.py` | Diff local vs remote snapshots |
| `sync/planner.py` | Convert selected diffs into `SyncJob`s (planned action + pending status) |
| `sync/executor.py` | rsync job execution |
| `mounts/nfs.py` | NFS mount/unmount via subprocess |
| `state/` | JSON-persisted state (jobs, diffs, last scan) |
| `auth/` | Argon2 password hashing, signed session cookies, login brute-force throttle |
| `schedule/` | Cron-like scheduler for automatic scans (day-of-week + time) |
| `transport/api_server.py` | FastAPI routes (role-based) + SSE log stream + pure-ASGI auth middleware |
| `transport/api_client.py` | HTTP client for orchestrator → agent calls |
| `templates/` | Jinja2 templates (`index.html`, `login.html`, `setup.html`, `_auth_base.html`) |
| `static/` | Logo, favicons, PWA manifest — mounted at `/static` on orchestrator |
| `shared/enums.py` | All status/action enums (NodeRole, DiffStatus, JobStatus, AppStatus, …) |

### Configuration

Copy `config/config.example.toml` to `config/config.toml`. Key sections:

```toml
[app]
role = "orchestrator"   # or "agent"

[mount]
remote_host      = "192.168.1.100"
remote_share     = "/volume1/media"
local_mount_path = "/mnt/nas"

[sync]
sync_roots   = ["videos", "music"]
compare_mode = "size_mtime"             # or "checksum"
exclude_file = "./config/excludes.list" # gitignore-style patterns for scan + rsync

[[agents]]
name = "remote-node"
host = "192.168.1.x"
```

The excludes file is plain text, one pattern per line (`#` comments, `*`/`?`/`[..]` globs). It's passed to `rsync --exclude-from` and to `du --exclude=` during scan, and also filters top-level folders. Deploy the same file on both nodes so their scans compute identical sizes.

State is persisted to `./data/state/state.json`; scan snapshots under `./data/state/snapshots/`.

### Runtime Notes

- External binaries required on the host: `mount`, `umount`, `mountpoint`, `rsync`, `du` — this is a Linux-only tool.
- SSE endpoint `/api/stream` streams log lines to the HTML UI in real time.
- Python 3.10+ required; uses `tomllib` (stdlib) or falls back to `tomli`.
- First browser visit redirects to `/setup` to create the single orchestrator user; subsequent visits require login at `/login`.
- Auth state (username, argon2 hash, session-signing secret) is stored in `./data/auth.toml`.
- Auth middleware is pure-ASGI (not `BaseHTTPMiddleware`) to avoid breaking SSE streams on shutdown.

# Salt & Soil

**Salt & Soil** is a lightweight dual-agent directory synchronization tool for homelab environments with storage located at multiple physical sites.

It mirrors selected directories and **individual subdirectories** between two NAS systems using **on-demand NFS mounts** and **rsync-based transfers**, while allowing both systems to remain in sleep mode when idle.

The goal is simple:

> keep storage in sync without keeping NAS devices awake 24/7

---

## Why Salt & Soil exists

Most synchronization tools assume always-on infrastructure and permanent mounts.

In multi-location homelab setups this is often undesirable:

- NAS devices should stay asleep when idle
- remote locations are not always reachable
- permanent mounts create unnecessary coupling
- subdirectory-level sync control is limited in many existing tools

Salt & Soil solves this by mounting NFS shares only when needed and executing explicit synchronization actions on demand.

---

## How it works

Salt & Soil uses a simple orchestrated workflow:

Start → mount NFS → scan both sides → show differences → user selects actions per directory/subdirectory → execute rsync / delete operations → unmount → NAS devices return to sleep

Synchronization is intentionally **operator-triggered**, not continuous.

---

## Architecture

Salt & Soil runs as a small containerized service with:

- a Python backend (FastAPI)
- a lightweight web UI
- rsync as transfer engine
- dynamic NFS mount / unmount lifecycle

A companion mini-agent runs on the remote location to mount the remote NAS locally and expose sync actions safely.

This avoids mounting remote NAS systems directly over the internet.

---

## Current status

Salt & Soil is currently a working prototype.

At the moment:

- scan is triggered manually
- sync is triggered manually
- directory and subdirectory selection is operator-controlled
- state is stored locally (JSON-based)

Scheduling and automation may be added later.

---

## Deployment — Proxmox LXC Container

Salt & Soil is designed to run on a Proxmox LXC container.
Because the application manages its own NFS mount/unmount lifecycle via subprocesses, the container requires elevated privileges.

### Recommended container settings

| Parameter | Value | Reason |
|-----------|-------|--------|
| **Template** | Debian 12 (bookworm) | |
| **Disk** | 8 GB | OS ~2 GB + Python/venv ~500 MB + data, logs, snapshots |
| **Memory** | 512 MB (max 1024 MB) | FastAPI idle ~80 MB; headroom for rsync and scan spikes |
| **CPU cores** | 1 (2 for comfort) | rsync is I/O-bound, not CPU-bound |
| **Unprivileged** | **No — use privileged** | See note below |
| **Nesting** | No | Only required for Docker-in-LXC scenarios |

### Why privileged?

The application executes `mount` and `umount` as subprocesses.
Unprivileged LXC containers lack `CAP_SYS_ADMIN` and cannot perform NFS mounts — the call fails with `permission denied`.

Mounting the NFS share on the Proxmox host and bind-mounting it into an unprivileged container is technically possible, but breaks the on-demand mount lifecycle that is central to how Salt & Soil works.

For a homelab orchestrator, a privileged container is the pragmatic and correct choice.

### First-run test (orchestrator)

Once the container is running and the config is in place:

```bash
# 1. Bootstrap (installs system packages, creates venv, generates SSH key)
bash scripts/bootstrap.sh --role orchestrator

# 2. Copy and edit config
cp config/config.example.toml config/config.toml
nano config/config.toml

# 3. Verify NFS mount + directory scan
python -m salt_and_soil test-mount
```

`test-mount` mounts the NAS, scans all configured `sync_roots`, and opens a read-only web UI showing folder sizes.
No agent is required for this step. Use the "Unmount & Stop" button to cleanly shut down.

---

## Name origin

Salt & Soil refers to the two environments the tool was originally designed for:

one NAS located near the coast  
one NAS located inland

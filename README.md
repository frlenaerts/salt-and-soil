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

## Typical use case

Example setup:
Primary NAS (inland location)
⇅
Secondary NAS (coastal location)


Salt & Soil keeps selected datasets synchronized while allowing both NAS systems to remain idle when not actively syncing.

---

## Name origin

Salt & Soil refers to the two environments the tool was originally designed for:

one NAS located near the coast  
one NAS located inland

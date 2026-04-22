# Salt & Soil

**Salt & Soil** is a lightweight dual-node directory synchronization tool for environments with storage located at multiple physical sites.

It mirrors selected directories between two storage systems using **on-demand NFS mounts** and **rsync-based transfers**, while allowing both systems to remain in sleep mode when idle.

> keep storage in sync without keeping devices awake 24/7

---

## Development approach

Salt & Soil was developed using an AI-assisted workflow. The architecture, scope, and design decisions were defined manually, while AI tooling helped accelerate implementation, refactoring, and documentation.

---

## Why Salt & Soil exists

Most synchronization tools assume always-on infrastructure and permanent mounts.

In multi-location setups this is often undesirable:

- storage devices should stay asleep when idle
- remote locations are not always reachable
- permanent mounts create unnecessary coupling
- subdirectory-level sync control is limited in many existing tools

Salt & Soil solves this by mounting NFS shares only when needed and executing explicit synchronization actions on demand.

---

## How it works

1. Press **Start Scan** in the web UI
2. Both storage systems are mounted via NFS (orchestrator locally, agent remotely)
3. Both sides are scanned and compared
4. NFS mounts are released immediately after the scan
5. The UI shows a diff per directory: in sync, different, local only, remote only
6. Select actions per directory and press **Execute**
7. rsync transfers the selected directories over SSH via Tailscale
8. Both mounts are released again — storage devices return to sleep

Scans can be triggered manually or on a schedule, but the actual sync is always **operator-triggered** — never continuous, never automatic.

---

## Sync granularity

Each entry in `sync_roots` is scanned **one level deep**. The immediate subdirectories become the units of comparison — each shows up as its own row in the diff, with its own Sync / Skip / Pull / Push / Delete action. `rsync` then transfers that subdirectory recursively as a whole.

Concretely, with `sync_roots = ["projects", "archives"]` you get diff rows like `projects/alpha` or `archives/2024`. You cannot decide per-file or per-deeper-subfolder within one of those units — the whole subdirectory is one decision.

This matches use cases where each top-level subfolder is a natural unit. For finer-grained or deeper-recursive sync, this version is not the right fit; it may be revisited in a future release.

---

## Architecture

Two roles, each running on its own Linux host (bare metal, VM, or container):

| Role | Location | Responsibility |
|------|----------|----------------|
| **Orchestrator** | Site A (home) | Web UI, scan, compare, execute rsync |
| **Agent** | Site B (remote) | Mount remote storage, expose scan API |

The two containers communicate over **Tailscale** (WireGuard-based VPN). No router port forwarding is required.

---

## Scope and trust model

Salt & Soil is a **single-operator tool for a trusted network**, not a multi-tenant or hostile-network service. The design assumes:

- **The agent API is unauthenticated.** Anyone on the Tailscale network can call `/mount`, `/unmount`, and `/list` on the agent. Trust lives at the VPN layer — if you expose the agent port on a public network, it is wide open.
- **The orchestrator web UI has a single user account** (argon2-hashed password, signed session cookie, 5-strikes / 15-minute brute-force throttle). The throttle is in-memory and global — it resets on process restart and does not discriminate per IP.
- **Session cookies are not marked `secure`.** You can access the UI over plain HTTP on a trusted LAN; put it behind TLS (reverse proxy, Cloudflare Tunnel, …) before exposing it publicly.
- **Both containers run privileged with root SSH** between them. This is required for NFS mounts and rsync; it is not a hardened setup.
- **There is no CSRF token.** The SameSite=Lax cookie is the only cross-origin defense.

If your threat model includes hostile actors on your internal network, or you plan to expose this publicly without additional auth in front of it, this tool is — *for now* — not the right fit. The current model is sufficient for the author's personal use; stricter auth (agent tokens, CSRF, `secure` cookies) may land in a future version.

---

## Deployment

Salt & Soil runs on any Linux host with Python 3.10+, `rsync`, and the standard NFS client tools — bare metal, VM, or container. Because the application manages its own NFS mount/unmount lifecycle via subprocesses, it needs root (or a sufficiently capable container).

### Reference setup — LXC container

The specs below are what the author runs in an LXC container. Any other Linux environment works as long as NFS mounts succeed from within it.

Both the orchestrator and agent run on a **privileged** LXC container (privileged is required for NFS mount via subprocess). **NFS must be enabled inside the container** — most LXC managers ship it disabled by default.

#### Recommended container settings (both nodes)

| Parameter | Value | Reason |
|-----------|-------|--------|
| **Template** | any modern Linux with Python 3.10+ | author runs Debian 12 (bookworm); Ubuntu 22.04+ etc. should work too |
| **Disk** | 8 GB | OS + Python/venv + data, logs, snapshots |
| **Memory** | 512 MB (max 1024 MB) | FastAPI idle ~80 MB; headroom for rsync and scan |
| **CPU cores** | 1–2 | rsync is I/O-bound, not CPU-bound |
| **Unprivileged** | **No — use privileged** | Required for NFS mount via subprocess |

#### Required container features

The container needs two things beyond a default Debian LXC:

- **NFS support** — so `mount -t nfs …` works inside the container
- **TUN device access** — required by Tailscale (see [Networking](#networking--tailscale) below)

Concretely: the host must (1) allow the NFS kernel syscalls through the container's AppArmor/feature profile, and (2) bind-mount `/dev/net/tun` into the container. These are host-side changes — the container itself cannot grant them.

The exact commands depend on your LXC manager. For **Proxmox** (running on the Proxmox host, replace `<ID>` with the container ID):

```bash
# Enable NFS inside the container
pct set <ID> --features nfs=1

# Enable Tailscale (TUN device)
echo 'lxc.cgroup2.devices.allow = c 10:200 rwm
lxc.mount.entry = /dev/net/tun dev/net/tun none bind,create=file' >> /etc/pve/lxc/<ID>.conf

pct restart <ID>
```

For **Incus** / **LXD** / plain **lxc**, consult your manager's docs for the equivalent of "allow NFS mount" and "pass through `/dev/net/tun`".

**On Proxmox, these settings are not included in container backups** — re-apply them after a restore.

---

## Networking — Tailscale

Salt & Soil uses [Tailscale](https://tailscale.com) to connect the orchestrator and agent containers securely. No router port forwarding is needed.

Tailscale uses WireGuard and establishes a direct peer-to-peer connection between the two locations when possible — rsync traffic travels directly between sites without an external relay.

### Setup (once per container)

**1.** Create a free account at https://tailscale.com

**2.** Apply the LXC TUN device config (see above) on the Proxmox host

**3.** Inside each container:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now tailscaled
tailscale up
```

Open the login URL, sign in with your Tailscale account. Repeat for both containers.

**4.** Note the permanent Tailscale IP of the agent container:

```bash
tailscale ip
```

Use this `100.x.x.x` address in the orchestrator `config.toml` under `[[agents]]`.

---

## Installation

Download the latest release tarball from the [GitHub releases page](https://github.com/frlenaerts/salt-and-soil/releases) and run the bootstrap script on each container:

```bash
cd /opt
curl -L https://github.com/frlenaerts/salt-and-soil/releases/latest/download/salt-and-soil.tar.gz | tar xz
cd salt-and-soil
bash scripts/bootstrap.sh --role orchestrator   # or --role agent
```

Bootstrap installs system packages, creates the Python venv, creates required directories, and generates an SSH key pair.

---

## Configuration

```bash
cp config/config.example.toml config/config.toml
nano config/config.toml
```

Key settings for the **orchestrator**:

```toml
[app]
role      = "orchestrator"
node_name = "your-node-name"

[mount]
remote_host       = "192.168.1.x"     # IP of the local storage server
remote_share      = "/volume1/video"  # NFS export path
local_mount_path  = "/mnt/nas"
mount_retry_delay = 10                # seconds before retrying a failed mount (wake-up)

[sync]
sync_roots = ["Movies", "TV Series"]

[[agents]]
name              = "agent-01"
host              = "100.x.x.x"       # Tailscale IP of the agent
port              = 8080              # free to choose — must match agent's [server] port
ssh_host          = "100.x.x.x"       # same Tailscale IP
ssh_user          = "root"
ssh_key_file      = "/root/.ssh/saltsoil_key"
remote_mount_path = "/mnt/nas"
remote_share      = "/volume1/video"  # NFS share on the agent's storage
```

Orchestrator and agent can both use port `8080` when they run on separate hosts (different IPs). If you run them on the same host, give each one a distinct port under `[server]`.

Key settings for the **agent** — same file, different role:

```toml
[app]
role      = "agent"
node_name = "agent-01"

[mount]
remote_host      = "192.168.1.x"      # IP of the remote storage server
remote_share     = "/volume1/video"
local_mount_path = "/mnt/nas"
```

### Excluding files from scan + sync

Patterns to skip are kept in a plain-text file (`gitignore`-style) so you can edit them without touching the code. The file ships with the repo as `config/excludes.list` — edit it directly:

```text
# Synology
@eaDir
*@SynoEAStream
*@SynoResource
.SynologyWorkingDirectory

# macOS
.DS_Store

# Windows
Thumbs.db
desktop.ini
```

Referenced from `config.toml`:

```toml
[sync]
exclude_file = "./config/excludes.list"
```

One pattern per line; `#` starts a comment; `*`, `?`, `[..]` work (rsync/fnmatch style). The same file is applied by:

- **`du`** during scan (size calculation)
- **`rsync`** during sync (via `--exclude-from`)
- **Top-level folder filter** in the scanner

Deploy the **same file** on both orchestrator and agent so their scans compute identical sizes — otherwise you'll see false "Different" statuses.

### NFS export permissions

The storage server needs to allow NFS mounts from the container's IP. On **Synology**, go to **Control Panel → Shared Folder → [folder] → Edit → NFS Permissions** and add a rule for the container's local IP:

- Hostname/IP: `192.168.1.x` (IP of the LXC container on that network)
- Privilege: Read/Write
- Squash: No mapping
- Security: sys

On other storage systems (TrueNAS, a plain Linux NFS server, QNAP, …) look for the equivalent export-permission settings — the fundamentals (allowed client IP, read/write, root squash, sec mode) are the same.

---

## SSH key setup (orchestrator → agent)

Before copying the key, SSH on the **agent container** must allow root login and password authentication. Edit `/etc/ssh/sshd_config` on the agent:

```bash
nano /etc/ssh/sshd_config
```

Set or add these two lines:

```
PermitRootLogin yes
PasswordAuthentication yes
```

Restart SSH:

```bash
systemctl restart sshd
```

Now copy the orchestrator's key to the agent:

```bash
# On the orchestrator container
ssh-copy-id -i /root/.ssh/saltsoil_key.pub root@<agent-tailscale-ip>
```

Test the connection:

```bash
ssh -i /root/.ssh/saltsoil_key root@<agent-tailscale-ip>
```

Once key-based login works you can set `PasswordAuthentication no` again on the agent for security.

---

## Running

Install both nodes as a systemd service so they start automatically on boot:

```bash
bash scripts/install-service.sh
```

The service handles venv activation and `PYTHONPATH` automatically. Use standard systemd commands to manage it:

```bash
systemctl status salt-and-soil
systemctl restart salt-and-soil
journalctl -u salt-and-soil -f
```

Open the web UI at `http://<container-ip>:<port>` (default port 8080, configurable under `[server]` in `config.toml`). The agent runs as the same service on its own container — it exposes a REST API, no UI.

### First-run setup

On first visit the orchestrator redirects to `/setup` to create the single user account (username + password, minimum 8 characters). After that, access to the UI and `/api/*` requires a signed session cookie issued by `/login`. The "Remember me" checkbox extends the cookie lifetime to 30 days.

Passwords are hashed with **argon2** and the session-signing secret is rotated on every password change. Repeated failed logins are throttled: **5 failures** lock new attempts for **15 minutes**.

Auth state lives in `./data/auth.toml` — delete that file to start over.

---

## Scheduled scans

The **Schedule** tab in the UI lets you trigger automatic scans on selected days of the week at a fixed time (24h clock). Pick specific days, use the `Daily` / `Weekdays` / `Weekend` presets, or toggle the whole schedule off. When enabled, the header shows a small clock-chip with the next fire time.

A scheduled run behaves exactly like pressing **Start Scan** manually — it mounts, scans, presents the diff, and waits for you to press **Execute**. It does not auto-sync.

---

## Name origin

Salt & Soil refers to the two environments the tool was originally designed for:

one NAS located near the coast  
one NAS located inland

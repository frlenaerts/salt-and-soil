# Salt & Soil

**Salt & Soil** is a lightweight dual-node directory synchronization tool for homelab environments with storage located at multiple physical sites.

It mirrors selected directories between two NAS systems using **on-demand NFS mounts** and **rsync-based transfers**, while allowing both systems to remain in sleep mode when idle.

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

1. Press **Start Scan** in the web UI
2. Both NAS systems are mounted via NFS (orchestrator locally, agent remotely)
3. Both sides are scanned and compared
4. NFS mounts are released immediately after the scan
5. The UI shows a diff per directory: in sync, different, local only, remote only
6. Select actions per directory and press **Execute**
7. rsync transfers the selected directories over SSH via Tailscale
8. Both mounts are released again — NAS devices return to sleep

Synchronization is intentionally **operator-triggered**, not continuous.

---

## Architecture

Two roles, each running on a Proxmox LXC container:

| Role | Location | Responsibility |
|------|----------|----------------|
| **Orchestrator** | Site A (home) | Web UI, scan, compare, execute rsync |
| **Agent** | Site B (remote) | Mount remote NAS, expose scan API |

The two containers communicate over **Tailscale** (WireGuard-based VPN). No router port forwarding is required.

---

## Deployment — Proxmox LXC Container

Both the orchestrator and agent run on a Proxmox LXC container. Because the application manages its own NFS mount/unmount lifecycle via subprocesses, the container must be privileged.

### Recommended container settings (both nodes)

| Parameter | Value | Reason |
|-----------|-------|--------|
| **Template** | Debian 12 (bookworm) | |
| **Disk** | 8 GB | OS + Python/venv + data, logs, snapshots |
| **Memory** | 512 MB (max 1024 MB) | FastAPI idle ~80 MB; headroom for rsync and scan |
| **CPU cores** | 1–2 | rsync is I/O-bound, not CPU-bound |
| **Unprivileged** | **No — use privileged** | Required for NFS mount via subprocess |

### Required LXC features

Both NFS and Tailscale require additional device access. Run this on the **Proxmox host** for each container (replace `<ID>` with the container ID):

```bash
# Enable NFS inside the container
pct set <ID> --features nfs=1

# Enable Tailscale (TUN device)
echo 'lxc.cgroup2.devices.allow = c 10:200 rwm
lxc.mount.entry = /dev/net/tun dev/net/tun none bind,create=file' >> /etc/pve/lxc/<ID>.conf

pct restart <ID>
```

**These settings are not included in container backups** — re-apply them after a restore.

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

Run on each container after cloning the repository:

```bash
cd /opt
git clone https://github.com/frlenaerts/salt-and-soil.git
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
remote_host      = "192.168.1.x"      # IP of the local NAS
remote_share     = "/volume1/video"   # NFS export path
local_mount_path = "/mnt/nas"

[sync]
sync_roots = ["Movies", "TV Series"]

[[agents]]
name              = "agent-01"
host              = "100.x.x.x"       # Tailscale IP of the agent
port              = 8080
ssh_host          = "100.x.x.x"       # same Tailscale IP
ssh_user          = "root"
ssh_key_file      = "/root/.ssh/saltsoil_key"
remote_mount_path = "/mnt/nas"
remote_share      = "/volume1/video"  # NFS share on the agent's NAS
```

Key settings for the **agent** — same file, different role:

```toml
[app]
role      = "agent"
node_name = "agent-01"

[mount]
remote_host      = "192.168.1.x"      # IP of the remote NAS
remote_share     = "/volume1/video"
local_mount_path = "/mnt/nas"
```

### Synology NFS permissions

On each Synology NAS, go to **Control Panel → Shared Folder → [folder] → Edit → NFS Permissions** and add a rule for the container's local IP:

- Hostname/IP: `192.168.1.x` (IP of the LXC container on that network)
- Privilege: Read/Write
- Squash: No mapping
- Security: sys

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

```bash
cd /opt/salt-and-soil
source .venv/bin/activate
PYTHONPATH=src python -m salt_and_soil serve
```

Open the web UI at `http://<container-ip>:<port>` (default port 8080).

The agent runs the same command on its container — it exposes a REST API, no UI.

To install as a systemd service:

```bash
bash scripts/install-service.sh
```

---

## Name origin

Salt & Soil refers to the two environments the tool was originally designed for:

one NAS located near the coast  
one NAS located inland

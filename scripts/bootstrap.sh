#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Salt & Soil — bootstrap.sh
#  One-time setup on a new LXC container (Debian 12 / Ubuntu 24).
#
#  Usage:
#    bash scripts/bootstrap.sh
#    bash scripts/bootstrap.sh --role agent     # agent packages only
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROLE="${1:-orchestrator}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$APP_DIR/.venv"

echo "=== Salt & Soil bootstrap ($ROLE) ==="
echo "    App dir : $APP_DIR"
echo "    Venv    : $VENV"

# ── System packages ───────────────────────────────────────────────────────────
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    nfs-common \
    rsync \
    openssh-client \
    curl

echo "✓ System packages installed"

# ── Virtualenv ────────────────────────────────────────────────────────────────
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "✓ Python venv created at $VENV"

# ── Mount point ───────────────────────────────────────────────────────────────
mkdir -p /mnt/salt-and-soil
echo "✓ Mount point /mnt/salt-and-soil created"

# ── Data dirs ─────────────────────────────────────────────────────────────────
mkdir -p "$APP_DIR/data/state/snapshots" \
         "$APP_DIR/data/cache" \
         "$APP_DIR/data/logs" \
         "$APP_DIR/data/exports"
echo "✓ Data directories created"

# ── Config (only if not present) ──────────────────────────────────────────────
if [ ! -f "$APP_DIR/config/config.toml" ]; then
    cp "$APP_DIR/config/config.example.toml" "$APP_DIR/config/config.toml"
    echo "✓ config/config.toml created — edit this before first start!"
else
    echo "  config/config.toml already exists — not overwritten"
fi

if [ ! -f "$APP_DIR/config/excludes.list" ]; then
    cp "$APP_DIR/config/excludes.example.list" "$APP_DIR/config/excludes.list"
    echo "✓ config/excludes.list created — edit to add/remove ignore patterns"
else
    echo "  config/excludes.list already exists — not overwritten"
fi

# ── SSH key (orchestrator only) ───────────────────────────────────────────────
if [ "$ROLE" = "orchestrator" ] && [ ! -f "$HOME/.ssh/saltsoil_key" ]; then
    mkdir -p "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$HOME/.ssh/saltsoil_key" -N "" -C "saltsoil"
    echo ""
    echo "✓ SSH key created: $HOME/.ssh/saltsoil_key"
    echo ""
    echo "  Copy the public key to the agent machine:"
    echo "  ssh-copy-id -i ~/.ssh/saltsoil_key.pub root@<agent-ip>"
    echo ""
fi

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "  Next steps:"
echo "  1. Edit config/config.toml"
echo "  2. Configure NFS exports on the NAS(es)"
if [ "$ROLE" = "orchestrator" ]; then
echo "  3. Copy SSH key to agent: ssh-copy-id -i ~/.ssh/saltsoil_key.pub root@<agent-ip>"
echo "  4. Install systemd service: bash scripts/install-service.sh"
else
echo "  3. Install systemd service: bash scripts/install-service.sh"
fi

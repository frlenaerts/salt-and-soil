#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Salt & Soil — bootstrap.sh
#  Eenmalige installatie op een nieuwe LXC container (Debian 12 / Ubuntu 24).
#
#  Gebruik:
#    bash scripts/bootstrap.sh
#    bash scripts/bootstrap.sh --role agent     # enkel agent pakketten
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROLE="${1:-orchestrator}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$APP_DIR/.venv"

echo "=== Salt & Soil bootstrap ($ROLE) ==="
echo "    App dir : $APP_DIR"
echo "    Venv    : $VENV"

# ── Systeem pakketten ─────────────────────────────────────────────────────────
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    nfs-common \
    rsync \
    openssh-client \
    curl

echo "✓ Systeem pakketten geïnstalleerd"

# ── Virtualenv ────────────────────────────────────────────────────────────────
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "✓ Python venv aangemaakt in $VENV"

# ── Mount punt aanmaken ───────────────────────────────────────────────────────
mkdir -p /mnt/salt-and-soil
echo "✓ Mount punt /mnt/salt-and-soil aangemaakt"

# ── Data dirs ─────────────────────────────────────────────────────────────────
mkdir -p "$APP_DIR/data/state/snapshots" \
         "$APP_DIR/data/cache" \
         "$APP_DIR/data/logs" \
         "$APP_DIR/data/exports"
echo "✓ Data directories aangemaakt"

# ── Config aanmaken indien nog niet aanwezig ──────────────────────────────────
if [ ! -f "$APP_DIR/config/config.toml" ]; then
    cp "$APP_DIR/config/config.example.toml" "$APP_DIR/config/config.toml"
    echo "✓ config/config.toml aangemaakt — pas dit aan vóór de eerste start!"
else
    echo "  config/config.toml bestaat al — niet overschreven"
fi

# ── SSH key aanmaken (enkel orchestrator) ────────────────────────────────────
if [ "$ROLE" = "orchestrator" ] && [ ! -f "$HOME/.ssh/saltsoil_key" ]; then
    mkdir -p "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$HOME/.ssh/saltsoil_key" -N "" -C "saltsoil"
    echo ""
    echo "✓ SSH key aangemaakt: $HOME/.ssh/saltsoil_key"
    echo ""
    echo "  Kopieer de public key naar de agent NUC:"
    echo "  ssh-copy-id -i ~/.ssh/saltsoil_key.pub root@<agent-ip>"
    echo ""
fi

echo ""
echo "=== Bootstrap klaar ==="
echo ""
echo "  Volgende stappen:"
echo "  1. Pas config/config.toml aan"
echo "  2. NFS exports instellen op de NAS(sen)"
if [ "$ROLE" = "orchestrator" ]; then
echo "  3. SSH key kopiëren naar agent: ssh-copy-id -i ~/.ssh/saltsoil_key.pub root@<agent-ip>"
echo "  4. Systemd service installeren: bash scripts/install-service.sh"
else
echo "  3. Systemd service installeren: bash scripts/install-service.sh"
fi

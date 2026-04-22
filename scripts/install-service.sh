#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Installs Salt & Soil as a systemd service.
#  Run as root on the LXC container.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$APP_DIR/.venv"
SERVICE="salt-and-soil"

cat > "/etc/systemd/system/$SERVICE.service" << EOF
[Unit]
Description=Salt & Soil NAS sync
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=PYTHONPATH=$APP_DIR/src
Environment=SALTSOIL_CONFIG=$APP_DIR/config/config.toml
ExecStart=$VENV/bin/python -m salt_and_soil serve
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl start  "$SERVICE"

echo "✓ Systemd service '$SERVICE' installed and started"
echo ""
echo "  Status : systemctl status $SERVICE"
echo "  Logs   : journalctl -fu $SERVICE"
echo "  Stop   : systemctl stop $SERVICE"

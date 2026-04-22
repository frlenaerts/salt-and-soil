#!/bin/bash
# Manual start — useful for testing
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$APP_DIR/.venv"
CONFIG="${SALTSOIL_CONFIG:-$APP_DIR/config/config.toml}"

export PYTHONPATH="$APP_DIR/src"
export SALTSOIL_CONFIG="$CONFIG"

exec "$VENV/bin/python" -m salt_and_soil serve

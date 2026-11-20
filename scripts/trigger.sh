#!/bin/bash
# Bind this script to a global hotkey (e.g. in KDE System Settings) to toggle HARVIS.
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" "$DIR/scripts/trigger.py"

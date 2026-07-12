#!/usr/bin/env bash
# Start a virtual display for headed Chrome, then run the worker.
set -e
mkdir -p /tmp/.X11-unix 2>/dev/null || true
Xvfb "${DISPLAY:-:99}" -screen 0 1920x1080x24 -nolisten tcp &
exec python main.py

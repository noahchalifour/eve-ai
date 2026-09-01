#!/bin/sh
# Self-heals the ephemeral parts of the image against the one thing that
# persists: /home/eve (design doc: "Storage"). A pod reschedule loses
# everything `apt install`ed by hand; this replays it from a file the
# worker itself maintains, so a lost package need not become a pull request.
set -eu

PACKAGES_FILE="/home/eve/.eve/packages.txt"
mkdir -p /home/eve/.eve /home/eve/tasks

if [ -f "$PACKAGES_FILE" ]; then
    PACKAGES=$(grep -v '^[[:space:]]*#' "$PACKAGES_FILE" | tr '\n' ' ')
    if [ -n "$PACKAGES" ]; then
        sudo apt-get update
        # shellcheck disable=SC2086
        sudo apt-get install -y --no-install-recommends $PACKAGES
    fi
fi

Xvfb :99 -screen 0 1920x1080x24 &
fluxbox &
x11vnc -display :99 -forever -shared -rfbport 5900 -nopw &

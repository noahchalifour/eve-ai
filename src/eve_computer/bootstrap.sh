#!/bin/sh
# Self-heals the ephemeral parts of the image against the one thing that
# persists: /home/eve (design doc: "Storage"). A pod reschedule loses
# everything `apt install`ed by hand; this replays it from a file the
# worker itself maintains, so a lost package need not become a pull request.
set -eu

PACKAGES_FILE="/home/eve/.eve/packages.txt"
mkdir -p /home/eve/.eve /home/eve/tasks

# The package replay is best-effort, not a precondition for the desktop
# starting: a typo'd package name in packages.txt or a transient apt mirror
# failure must not crash-loop the whole pod (the self-heal mechanism
# becoming the outage, on a file nobody but the automated worker writes).
# `set +e`/`set -e` brackets only this section - `set -u` stays on for the
# rest of the script.
if [ -f "$PACKAGES_FILE" ]; then
    PACKAGES=$(grep -v '^[[:space:]]*#' "$PACKAGES_FILE" | tr '\n' ' ')
    if [ -n "$PACKAGES" ]; then
        set +e
        sudo apt-get update
        if [ $? -ne 0 ]; then
            echo "bootstrap.sh: apt-get update failed; continuing without replaying packages.txt" >&2
        else
            # shellcheck disable=SC2086
            sudo apt-get install -y --no-install-recommends $PACKAGES
            if [ $? -ne 0 ]; then
                echo "bootstrap.sh: apt-get install failed for one or more packages in packages.txt; continuing" >&2
            fi
        fi
        set -e
    fi
fi

Xvfb :99 -screen 0 1920x1080x24 &
fluxbox &
x11vnc -display :99 -forever -shared -rfbport 5900 -nopw &

#!/usr/bin/env bash
# Explicit human intervention required to re-arm after ANY Pi reboot or stop.
# A file in /run disappears after power loss; systemd refuses to start without it.
set -euo pipefail

if (( EUID != 0 )); then
    echo "Use: sudo bash scripts/start_kiln.sh" >&2
    exit 2
fi

if systemctl is-active --quiet kiln-controller.service; then
    echo "[KILN] Controller already running."
    exit 0
fi

if ! systemctl is-active --quiet kiln-relay-master.service; then
    echo "[KILN] Relay master is not running. Kiln remains OFF." >&2
    exit 2
fi

# Do not arm while any other supervisor may already be controlling relays.
# The supervisor also checks its own exclusive flock lock.
if ps -C kiln -o stat= | grep -Eq '^[[:space:]]*[^Z[:space:]]'; then
    echo "[KILN] Another kiln process is running; stop it first." >&2
    exit 2
fi

if pgrep -f '[/]bin/bash .*run_kiln_recovering.sh' >/dev/null; then
    echo "[KILN] A kiln recovery supervisor is already running; stop it first." >&2
    exit 2
fi

# This root-owned marker is deliberately volatile (tmpfs); a reboot clears it.
install -m 0600 /dev/null /run/kiln-controller-manual-arm
systemctl reset-failed kiln-controller.service
if ! systemctl start kiln-controller.service; then
    rm -f /run/kiln-controller-manual-arm
    echo "[KILN] Start failed. Manual-start permission removed." >&2
    exit 1
fi

sleep 2
if ! systemctl is-active --quiet kiln-controller.service; then
    rm -f /run/kiln-controller-manual-arm
    echo "[KILN] Service exited. Manual-start permission removed." >&2
    systemctl status kiln-controller.service --no-pager || true
    exit 1
fi

echo "[KILN] Controller started by explicit operator action."
echo "[KILN] Reboot/power failure will NOT restart heating automatically."
echo "To stop: sudo systemctl stop kiln-controller.service"

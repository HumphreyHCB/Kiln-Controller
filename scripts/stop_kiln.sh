#!/usr/bin/env bash
# Zero-argument deliberate kiln stop: stop controller, verify processes have
# exited, command ALL_OFF through independent GPIO owner, refresh GitHub.
set -euo pipefail

echo "[KILN STOP] Requested: stopping controller and verifying relay OFF." >&2
if (( $# != 0 )); then
    echo "[KILN STOP] This script takes no arguments." >&2
    exit 2
fi
if (( EUID != 0 )); then
    echo "[KILN STOP] Run: sudo bash scripts/stop_kiln.sh" >&2
    exit 2
fi
cd "$(dirname "$(realpath "$0")")/.."

# Even if service is inactive, explicitly disarm; no further manual run
# is permitted until start_kiln.sh arms the new session.
systemctl stop kiln-controller.service
rm -f /run/kiln-controller-manual-arm

if systemctl is-active --quiet kiln-controller.service ||
   ps -C kiln -o stat= | grep -Eq '^[[:space:]]*[^Z[:space:]]' ||
   pgrep -f '[r]un_kiln_recovering.sh' >/dev/null; then
    echo "[KILN STOP] DANGER: A kiln process/supervisor may still be running." >&2
    echo "[KILN STOP] Cannot safely query the single-client relay master." >&2
    exit 1
fi

echo "[KILN STOP] Controller stopped; requesting explicit ALL_OFF acknowledgement." >&2
if ! python3 force_relays_off.py; then
    echo "[KILN STOP] DANGER: Could not confirm relay-master ALL_OFF. Inspect physically!" >&2
    exit 1
fi

echo "[KILN STOP] Heater/humidifier/fan commanded OFF by relay master." >&2
echo "[KILN STOP] Re-enabling monitoring and updating GitHub Issue now." >&2
if ! systemctl enable --now kiln-github-status.timer; then
    echo "[KILN STOP] WARNING: Could not enable GitHub status timer." >&2
fi
if ! systemctl start kiln-github-status.service; then
    echo "[KILN STOP] WARNING: GitHub status update failed; inspect monitoring journal." >&2
fi
echo "[KILN STOP] Complete. Future starts require explicit sudo start_kiln.sh." >&2

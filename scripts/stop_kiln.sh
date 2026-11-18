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
# The relay verification helper also opens the supervisor flock file. Run it
# as the service user so it cannot leave a root-owned lock that blocks restart.
if ! sudo -u humphrey /usr/bin/python3 scripts/confirm_stopped_relays.py; then
    echo "[KILN STOP] DANGER: ALL_OFF / readback of four relay channels not confirmed. Inspect physically!" >&2
    exit 1
fi

echo "[KILN STOP] All four relay channels report OFF in the GPIO-owner software state." >&2
echo "[KILN STOP] Re-enabling monitoring and updating GitHub Issue now." >&2
if ! systemctl enable --now kiln-github-status.timer; then
    echo "[KILN STOP] WARNING: Could not enable GitHub status timer." >&2
fi
if ! systemctl start kiln-github-status.service; then
    echo "[KILN STOP] WARNING: GitHub status update failed; inspect monitoring journal." >&2
fi
echo "[KILN STOP] Posting operator action comment to GitHub Issue #1." >&2
if ! sudo -u humphrey /usr/bin/python3 scripts/post_operator_comment.py stop; then
    echo "[KILN STOP] WARNING: Operator comment failed; control action already completed." >&2
fi
echo "[KILN STOP] Complete. Future starts require explicit sudo start_kiln.sh." >&2

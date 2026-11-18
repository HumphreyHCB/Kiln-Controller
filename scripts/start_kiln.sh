#!/usr/bin/env bash
# Zero-argument manual start. A full clean C++ build happens ONLY after
# verifying there is no active kiln service/process/supervisor.
# This helper never bypasses the manual-start interlock after a reboot.
set -euo pipefail

echo "[KILN START] Requested: safe clean rebuild and manual kiln startup." >&2
if (( $# != 0 )); then
    echo "[KILN START] This script takes no arguments." >&2
    exit 2
fi
if (( EUID != 0 )); then
    echo "[KILN START] Run: sudo bash scripts/start_kiln.sh" >&2
    exit 2
fi
cd "$(dirname "$(realpath "$0")")/.."

if systemctl is-active --quiet kiln-controller.service; then
    echo "[KILN START] Kiln already running; refusing to rebuild beneath a live controller." >&2
    exit 2
fi
if ! systemctl is-active --quiet kiln-relay-master.service; then
    echo "[KILN START] Relay-master service is not running. Heater remains OFF." >&2
    exit 2
fi
if ps -C kiln -o stat= | grep -Eq '^[[:space:]]*[^Z[:space:]]'; then
    echo "[KILN START] Another kiln process is running. Stop it first." >&2
    exit 2
fi
if pgrep -f '[r]un_kiln_recovering.sh' >/dev/null; then
    echo "[KILN START] A recovery supervisor is already running; refusing clean rebuild." >&2
    exit 2
fi

# Keep the manual arm absent during the build. A failed build cannot heat.
rm -f /run/kiln-controller-manual-arm
echo "[KILN START] Removing old CMake build directory; preserving configuration and run logs." >&2
cmake -E rm -rf build
echo "[KILN START] Configuring and compiling as humphrey (service user)." >&2
# All generated files MUST be writable by the unprivileged service user:
# the recovery supervisor also runs 'cmake --build build' later.
sudo -u humphrey cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
sudo -u humphrey cmake --build build --parallel 2

# A deliberate operator start acknowledges and resets any prior timeout streak.
rm -f logs/kiln_heater_timeout_streak.txt

# The volatile root-only marker remains required for each deliberate start.
echo "[KILN START] Build succeeded; authorising this run and starting kiln." >&2
install -m 0600 /dev/null /run/kiln-controller-manual-arm
systemctl reset-failed kiln-controller.service
if ! systemctl start kiln-controller.service; then
    rm -f /run/kiln-controller-manual-arm
    echo "[KILN START] Service start failed. Manual permission removed." >&2
    exit 1
fi

sleep 2
if ! systemctl is-active --quiet kiln-controller.service; then
    rm -f /run/kiln-controller-manual-arm
    echo "[KILN START] Service exited; manual permission removed." >&2
    systemctl status kiln-controller.service --no-pager || true
    exit 1
fi

echo "[KILN START] Started successfully. Auto-start remains locked after reboot." >&2
echo "[KILN START] Re-enabling monitoring and requesting immediate GitHub update." >&2
if ! systemctl enable --now kiln-github-status.timer; then
    echo "[KILN START] WARNING: Could not enable GitHub status timer." >&2
fi
if ! systemctl start kiln-github-status.service; then
    echo "[KILN START] WARNING: GitHub status update failed; inspect monitoring journal." >&2
fi
echo "[KILN START] Posting operator action comment to GitHub Issue #1." >&2
if ! sudo -u humphrey /usr/bin/python3 scripts/post_operator_comment.py start; then
    echo "[KILN START] WARNING: Operator comment failed; control action already completed." >&2
fi
echo "[KILN START] To stop: sudo bash scripts/stop_kiln.sh" >&2

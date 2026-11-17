#!/usr/bin/env bash
# Install the independent plot timer. DOES NOT stop/start the kiln, its relay
# master, or the existing 20-minute GitHub Issue monitoring timer.
set -euo pipefail

cd "$(dirname "$(realpath "$0")")/.."
echo "[KILN PLOTS] Installing 12-hour plot publisher (controller untouched)."

# The service runs as humphrey and uses system Python on Raspberry Pi OS Lite.
# Debian/Raspberry Pi OS manages system Python packages with apt.
sudo apt-get update
sudo apt-get install -y python3-matplotlib
/usr/bin/python3 -c 'import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot'

if [[ ! -s /home/humphrey/.config/kiln/github-token ]]; then
    echo "[KILN PLOTS] GitHub token missing at /home/humphrey/.config/kiln/github-token" >&2
    echo "[KILN PLOTS] Add Contents: Read and write to its repository permissions." >&2
    exit 1
fi

sudo install -m 0644 systemd/kiln-plot-publisher.service \
    /etc/systemd/system/kiln-plot-publisher.service
sudo install -m 0644 systemd/kiln-plot-publisher.timer \
    /etc/systemd/system/kiln-plot-publisher.timer
sudo systemctl daemon-reload
sudo systemctl enable --now kiln-plot-publisher.timer

echo "[KILN PLOTS] Publishing an initial chart set now..."
sudo systemctl start kiln-plot-publisher.service
echo "[KILN PLOTS] Published. Next runs: midnight/noon UTC, every 12 hours."
systemctl list-timers --all kiln-plot-publisher.timer

#!/usr/bin/env bash
# Installs only monitoring timer; never starts or restarts the kiln.
set -euo pipefail
cd "$(dirname "$(realpath "$0")")/.."
sudo install -m 0644 systemd/kiln-controller.service /etc/systemd/system/kiln-controller.service
sudo install -m 0644 systemd/kiln-github-status.service /etc/systemd/system/kiln-github-status.service
sudo install -m 0644 systemd/kiln-github-status.timer /etc/systemd/system/kiln-github-status.timer
sudo systemctl daemon-reload
sudo systemctl enable --now kiln-github-status.timer
printf '\nMonitoring timer installed; the kiln was NOT started or stopped.\n'
printf 'To publish now: sudo systemctl start kiln-github-status.service\n'
printf 'After stopping any previous timed kiln process, enable permanent operation with:\n'
printf '  sudo systemctl enable --now kiln-controller.service\n'

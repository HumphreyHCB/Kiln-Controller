#!/usr/bin/env bash
# Installs only monitoring timer; never starts or restarts the kiln.
set -euo pipefail
cd "$(dirname "$(realpath "$0")")/.."
sudo install -m 0644 systemd/kiln-controller.service /etc/systemd/system/kiln-controller.service
sudo install -m 0644 systemd/kiln-github-status.service /etc/systemd/system/kiln-github-status.service
sudo install -m 0644 systemd/kiln-github-status.timer /etc/systemd/system/kiln-github-status.timer
sudo systemctl daemon-reload
# Enable the unit for systemd bookkeeping, but the /run interlock prevents
# it from starting after a boot until an operator explicitly re-arms it.
sudo systemctl enable kiln-controller.service
sudo systemctl enable --now kiln-github-status.timer
printf '\nMonitoring timer installed; kiln was NOT started or stopped.\n'
printf 'To publish now: sudo systemctl start kiln-github-status.service\n'
printf 'Kiln is boot-locked until explicit operator intervention.\n'
printf 'After stopping any previous timed kiln process, manually start with:\n'
printf '  sudo bash scripts/start_kiln.sh\n'
printf 'After a reboot, it remains OFF until this command is run again.\n'

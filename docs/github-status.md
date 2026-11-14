# GitHub Issue monitoring and reboot-safe kiln operation

**[Open the live kiln status Issue #1](https://github.com/HumphreyHCB/Kiln-Controller/issues/1)**

This installation adds two independent services:

1. **Kiln controller** — uses the existing C++ program and recovery supervisor, but **never starts automatically after any Pi reboot**. It must be re-armed locally by an operator.
2. **GitHub monitoring** — starts on Pi boot, publishes the newest CSV/status every 20 minutes, and reports possible unexpected interruptions even while the controller remains OFF.

No changes to relay-control C++ or the relay master are needed. Python uses the standard library.

## Setup on Raspberry Pi

1. Merge [PR #2](https://github.com/HumphreyHCB/Kiln-Controller/pull/2), then:

   ```bash
   cd ~/repos/Kiln-Controller
   git pull --ff-only
   python3 -m unittest discover -s tests -v
   ```

2. Create a [fine-grained GitHub personal access token](https://github.com/settings/personal-access-tokens/new). Resource owner: **HumphreyHCB**, repository access: **Only select repositories → Kiln-Controller**, repository permission: **Issues → Read and write**; Metadata → Read-only is automatic. No Contents/Actions permission needed. Keep this token private.

3. Save the token **outside** the repository, using hidden input:

   ```bash
   install -d -m 700 ~/.config/kiln
   read -rsp 'GitHub token: ' GITHUB_TOKEN; echo
   printf '%s\n' "$GITHUB_TOKEN" > ~/.config/kiln/github-token
   unset GITHUB_TOKEN
   chmod 600 ~/.config/kiln/github-token
   ```

4. Install the services. **This will not start/stop the kiln**:

   ```bash
   bash scripts/install_github_status.sh
   sudo systemctl start kiln-github-status.service
   sudo journalctl -u kiln-github-status.service -n 30 --no-pager
   ```

The installer **enables** the controller unit but does not **start** it. Enabling does not bypass the boot interlock: systemd's `ExecCondition` refuses to launch it without a marker in volatile /run. This marker is removed on power loss, reboot, or service stop. The monitor timer has no such interlock and can report a fault after power returns.

## Explicit start and stop — safety-critical

Before starting, stop/finish any earlier timed/manual controller and inspect existing processes and the relay-master service:

```bash
pgrep -af 'run_kiln_recovering|build/kiln'
sudo systemctl status kiln-relay-master.service
```

After verifying the heater setup is safe, use **only the explicit start helper**:

```bash
cd ~/repos/Kiln-Controller
sudo bash scripts/start_kiln.sh
sudo systemctl status kiln-controller.service
```

Stop manually with:

```bash
sudo systemctl stop kiln-controller.service
```

The start helper checks the relay-master service and existing kiln processes, writes the current-boot authorization marker (`/run/kiln-controller-manual-arm`), then starts the controller. A direct `systemctl start kiln-controller.service` **without this marker** will skip running the program.

The existing recovery supervisor may retry stale Bluetooth data at most three times *during that manually authorized run*. If the supervisor exits, the unit has `Restart=no` and its post-stop action clears the authorization marker. It does **not** restart automatically after failures.

## Unexpected loss of power / abrupt reboot

Desired behavior:

1. Controller is running. `kiln_controller_lifecycle.json` (persisted under logs) records the start time and Linux boot ID.
2. Power fails abruptly. No shutdown script or GitHub update can run during an outage. The /run authorization marker disappears.
3. Pi powers on again. The **relay master** starts normally, but the **kiln controller does not start** because the volatile authorization marker is absent.
4. The monitoring timer starts after boot and network availability. If the previous saved lifecycle state says **running** and its boot ID differs from the current boot, Issue #1 displays **POSSIBLE UNEXPECTED POWER LOSS — KILN LOCKED OFF**, the last pre-reboot CSV timestamp, and **MANUAL RESTART REQUIRED**.
5. The Issue will keep displaying the interruption history even after an operator starts the kiln again.

A **normal shutdown/reboot** normally allows the systemd post-stop hook to record a clean stop; the kiln still stays OFF after reboot. A **service failure without a reboot** records an error and the controller remains OFF; manual restart is required.

You cannot reliably tell whether an unclean reboot was caused by a mains failure, an OS crash, or a forced reset. You cannot determine the exact power-loss instant: the final CSV row is only the last *recorded* measurement.

**The monitor cannot report while power or internet is absent.** It updates the issue on the next successful check after boot (typically within 20 minutes, dependent on network/scheduling). GitHub Issue body edits are not guaranteed to push a notification to your phone; inspect Issue #1 when needed.

## Useful commands

- **GitHub status:** `sudo systemctl start kiln-github-status.service`
- **Monitor logs:** `sudo journalctl -u kiln-github-status.service -n 30 --no-pager`
- **Timer:** `systemctl list-timers kiln-github-status.timer`
- **Controller logs:** `sudo journalctl -u kiln-controller.service -n 40 --no-pager`
- **Disable monitoring:** `sudo systemctl disable --now kiln-github-status.timer`

## Safety limitations

- GitHub monitoring is **read-only** and not a safety control. Last reported relay states are *commanded software states*, not electrical contact measurements; a stale report must not be presented as current.
- The GPIO relay-master's independent watchdog remains required. **A software-controlled relay on an active-low GPIO HAT is not proof of a safely isolated heater during boot.** The hardware must power up in a verified safe/off state, with an independent correctly rated high-temperature cutoff.
- The live Issue and its history are in a **public GitHub repository**.
- On disk corruption, a damaged SD card, lost files, or a failed systemd unit, even the post-boot report may not be delivered. An off-Pi heartbeat checker would be needed for alerts during an outage.

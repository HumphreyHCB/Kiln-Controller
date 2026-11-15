# GitHub Issue monitoring and reboot-safe kiln operation

**[Open the live kiln status Issue #1](https://github.com/HumphreyHCB/Kiln-Controller/issues/1)**

This installation adds two independent services. **When the controller is stopped and unarmed, the status publisher also performs a guarded `ALL_OFF` request against the independent GPIO relay master.** The master acknowledges the write, and the publisher reads back the master's commanded state for GPIO 26, 19, 13 and 6. This is positive confirmation from the Pi-side GPIO owner, not merely an assumption from the controller being absent.

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

## Understanding the GitHub status labels

- **Kiln startup permission** — "Granted for this run" means the kiln was manually authorised during this boot. It switches to "Not granted — manual start required" when the controller stops or the Pi reboots; the permission is not remembered across reboots.
- **Relay OFF check (stopped kiln only)** — "Not applicable — kiln is running" is normal: checking with ALL_OFF would interfere with heating. When the controller is stopped and disarmed, the monitor can command ALL_OFF and read back all four relay-master states. It then reports "Confirmed OFF" if acknowledged, or "OFF not verified" if the check fails. This verifies the *Pi-side commanded* state, not physical voltage or relay contacts.
- **Kiln process supervisor (Linux service)** — the systemd unit managing the existing recovery supervisor script.
- **Kiln C++ temperature/humidity controller** — the actual executable reading the Bluetooth beacon and making heating, humidification and fan decisions.
- **Independent safety controller (relay watchdog)** — the separate GPIO-owner service running on the **same Raspberry Pi**. It maintains the 10-second heartbeat timeout and commands outputs OFF on disconnect. "Independent" means separate from the C++ control process, **not an independent hardware safety circuit**.

## Alerts and GitHub notifications

The ordinary **20-minute status refresh only edits Issue #1**; it does not create a comment, commit, push, or notification workflow run. Relay ON/OFF changes during normal regulation never create alert comments.

**Significant events** do create Issue comments:

- **Successful controller start** — once after a *new* service run has working Bluetooth telemetry and a healthy supervisor and relay master. An already-running kiln is treated as a baseline when the feature is first installed.
- **Unexpected controller stop / service failure** — once per new failed service exit, even if the process stops between two report intervals.
- **Suspected unexpected Pi reboot** — once per interrupted run after power and internet return. This does not establish the cause of the interruption.
- **CHECK KILN or OFF not verified** — once when a fault condition is first observed, not every reporting cycle. A cleared and later recurring fault generates a new alert.

Normal operation and deliberate stops remain silent.

### How a GitHub notification reaches the owner

The Pi uses a fine-grained personal access token from the owner's account, so comments authored by the Pi **as that same account** normally do not notify the owner.

A separate GitHub Actions workflow at .github/workflows/kiln-event-notifications.yml runs **only** after a new event comment from HumphreyHCB on Issue #1. The GitHub Actions bot then posts a short comment with an **@HumphreyHCB mention** linking directly to the incident. Normal 20-minute Issue body edits never trigger that workflow. The workflow ignores its own bot comments.

Each significant event therefore leaves **two Issue comments**: detailed event information from the Pi, then a bot mention pointing to it. The Pi token still requires only **Issues: Read and write**. GitHub Actions uses the built-in workflow token with **Issues: write**.

Enable web/email/mobile notifications under [GitHub notification settings](https://github.com/settings/notifications), and enable GitHub Mobile push notifications on your phone if wanted. GitHub may group, delay or suppress notifications depending on the account/device settings. This is **not an emergency paging system**.

**Latency:** new events are detected at the next monitor run (currently up to about 20 minutes plus networking). The Pi cannot notify anyone while it is powered off or offline. A failed comment request is persisted locally and retried on later timer runs. If you need an alert while the Pi is offline, use a separate off-Pi heartbeat monitor.

The local event checkpoint is stored at logs/kiln_github_notification_state.json (inside the ignored logs directory), so ordinary 20-minute updates do not produce duplicate alerts.

## Useful commands

- **GitHub status:** `sudo systemctl start kiln-github-status.service`
- **Monitor logs:** `sudo journalctl -u kiln-github-status.service -n 30 --no-pager`
- **Timer:** `systemctl list-timers kiln-github-status.timer`
- **Controller logs:** `sudo journalctl -u kiln-controller.service -n 40 --no-pager`
- **Disable monitoring:** `sudo systemctl disable --now kiln-github-status.timer`

## Safety limitations

- GitHub monitoring does not command any relay ON, but when the kiln is stopped it explicitly requests `ALL_OFF` and checks the master's internal `GET` states. It does **not** interrupt an active supervisor: it first takes the supervisor's exclusive flock lock and checks the controller/process/interlock before contacting the master's single-client socket. This is not a safety control. The OFF verification means the GPIO owner accepted `ALL_OFF` and its software state reports OFF for all four pins. It does not independently measure voltage or contact position. If this verification fails or cannot safely run, the Issue reports **OFF NOT VERIFIED** rather than falsely claiming OFF. During normal operation, relay states come from the CSV and are commanded software states, not independent electrical contact measurements.
- The GPIO relay-master's independent watchdog remains required. **A software-controlled relay on an active-low GPIO HAT is not proof of a safely isolated heater during boot.** The hardware must power up in a verified safe/off state, with an independent correctly rated high-temperature cutoff.
- The live Issue and its history are in a **public GitHub repository**.
- On disk corruption, a damaged SD card, lost files, or a failed systemd unit, even the post-boot report may not be delivered. An off-Pi heartbeat checker would be needed for alerts during an outage.

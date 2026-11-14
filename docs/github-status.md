# Free GitHub Issue kiln monitoring

[Live status Issue #1](https://github.com/HumphreyHCB/Kiln-Controller/issues/1) is a read-only status page updated from the Pi's existing CSV logs every 20 minutes. Python standard library only; no changes to C++, relay master, or thermostat settings.

## Setup (on Raspberry Pi)

1. Merge the monitoring PR and run:

   `cd ~/repos/Kiln-Controller && git pull --ff-only`

2. Create a [fine-grained GitHub personal access token](https://github.com/settings/personal-access-tokens/new). Resource owner: **HumphreyHCB**; repository access: **Only select repositories → Kiln-Controller**; repository permissions: **Issues → Read and write**; **Metadata → Read-only** is automatic. No Contents/Administration/Actions permission is needed. Never share or commit the token.

3. Save the token outside the repo (the input is hidden):

   ```bash
   install -d -m 700 ~/.config/kiln
   read -rsp 'GitHub token: ' GITHUB_TOKEN; echo
   printf '%s\n' "$GITHUB_TOKEN" > ~/.config/kiln/github-token
   unset GITHUB_TOKEN
   chmod 600 ~/.config/kiln/github-token
   ```

4. Install the timer (does NOT start or stop the kiln):

   ```bash
   bash scripts/install_github_status.sh
   sudo systemctl start kiln-github-status.service
   sudo journalctl -u kiln-github-status.service -n 30 --no-pager
   ```

5. See [Issue #1](https://github.com/HumphreyHCB/Kiln-Controller/issues/1). The script PATCHes this existing Issue rather than creating duplicates. If an old timed or manual kiln process is running, the report will say CHECK KILN until the permanent service is enabled, even if CSV readings are fresh.

## Switch the kiln to permanent operation

Do **not** run two instances. First finish/stop any existing timed systemd run or manual supervisor and check active processes:

```bash
pgrep -af 'run_kiln_recovering|build/kiln'
sudo systemctl status kiln-relay-master.service
```

When the old controller is stopped and the relay-master is healthy:

```bash
sudo systemctl enable --now kiln-controller.service
sudo systemctl status kiln-controller.service
```

The existing recovery supervisor has at most three sensor-recovery attempts; systemd does NOT restart it indefinitely. If it exits, the relay master must de-energise outputs. Hardware independent over-temperature protection is still essential.

## Commands

- Latest report: `sudo journalctl -u kiln-github-status.service -n 30 --no-pager`
- Trigger report: `sudo systemctl start kiln-github-status.service`
- View timer: `systemctl list-timers kiln-github-status.timer`
- Stop reporting: `sudo systemctl disable --now kiln-github-status.timer`
- Stop permanent kiln: `sudo systemctl stop kiln-controller.service`

## Limits and safety

- GitHub report is delayed by up to 20 minutes, plus scheduling/internet delays. When power or network fails, the issue stays frozen at its last successful update; reports over 40 minutes old are **stale**, not necessarily proof of kiln status. This does **not** send push notifications.
- The repo is **public**; anyone can see the report. Consider a private repository if the measurements shouldn't be public.
- The script inspects the newest CSV's last 256 KiB, systemd, and the kiln process. Relays are **commanded**, not physically confirmed.
- If GitHub is unavailable or the token expires, the publisher fails without changing kiln control.

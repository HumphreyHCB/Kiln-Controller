#!/usr/bin/env python3
"""Kiln CSV telemetry publisher for GitHub Issue #1.

During stopped/unarmed periods this monitor explicitly requests ALL_OFF from
the single-client relay master and checks its commanded state under the
supervisor lock. It never commands a relay ON.
Uses only Python's standard library.
"""
import csv
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

from kiln_service_state import boot_id, read_state, last_csv_measurement
from verify_relays_off import confirm_off

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
TOKEN_FILE = Path.home() / ".config/kiln/github-token"
REPO = "HumphreyHCB/Kiln-Controller"
ISSUE = 1
WINDOW = 20 * 60
ARM_FILE = Path("/run/kiln-controller-manual-arm")


def active_service(name):
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def kiln_alive():
    try:
        result = subprocess.run(
            ["ps", "-C", "kiln", "-o", "stat="],
            capture_output=True, text=True, check=True, timeout=3
        )
        return any(s.strip() and not s.strip().startswith("Z")
                   for s in result.stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        return False


def timestamp(raw):
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (ValueError, AttributeError):
        return None


def number(raw):
    try:
        return float(raw) if raw not in ("", None) else None
    except (ValueError, TypeError):
        return None


def latest_csv():
    try:
        candidates = sorted(LOGS.glob("kiln_*.csv"),
                            key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return None, []
    for path in candidates:
        try:
            with path.open("rb") as stream:
                header = stream.readline()
                header_end = stream.tell()
                end = stream.seek(0, os.SEEK_END)
                start = max(header_end, end - 256 * 1024)
                stream.seek(start)
                if start > header_end:
                    stream.readline()  # Skip partial row at start of tail
                tail = stream.read()
            text = (header + tail).decode("utf-8")
            rows = list(csv.DictReader(io.StringIO(text)))
            valid = [row for row in rows if timestamp(row.get("timestamp_utc"))]
            if valid:
                return path, valid
        except (OSError, UnicodeError, csv.Error):
            pass
    return None, []


def display(value, unit="", precision=1):
    return f"{value:.{precision}f}{unit}" if value is not None else "Unavailable"


def switch(value, trustworthy):
    if not trustworthy:
        return "UNKNOWN (no fresh telemetry)"
    return {"1": "ON", "0": "OFF"}.get(value, "UNKNOWN")


def lifecycle_section():
    """Display persistent start/stop history, including suspected unclean boots."""
    state = read_state()
    events = state.get("events", [])
    if not isinstance(events, list):
        events = []

    current_boot = boot_id()
    if (state.get("status") == "running" and current_boot
            and state.get("boot_id") and state["boot_id"] != current_boot):
        events = events + [{
            "kind": "suspected_unclean_reboot",
            "detected_utc": "Current boot (controller has not re-started)",
            "detail": "Controller was running on a previous boot; no clean-stop record",
            "previous_started_utc": state.get("started_utc"),
            "last_measurement_before_restart_utc": last_csv_measurement(),
        }]
    if not events:
        return "No unexpected interruptions have been recorded since tracking was enabled."

    lines = []
    names = {
        "suspected_unclean_reboot": "⚠️ Possible unexpected power loss / reboot",
        "previous_run_not_cleanly_closed": "⚠️ Previous controller run not cleanly closed",
        "controller_service_failure": "⚠️ Controller service failed",
    }
    for entry in reversed(events[-5:]):
        kind = entry.get("kind", "unknown")
        description = names.get(kind, kind.replace("_", " "))
        when = entry.get("detected_utc", "Unknown time")
        detail = entry.get("detail", "")
        last_measurement = entry.get("last_measurement_before_restart_utc")
        line = f"- **{description}** — detected {when}. {detail}"
        if last_measurement:
            line += f"; last pre-restart CSV measurement: {last_measurement}"
        lines.append(line)
    return "\n".join(lines)


def report(now=None):
    now = now or dt.datetime.now(UTC)
    path, rows = latest_csv()
    row = rows[-1] if rows else {}
    recorded = timestamp(row.get("timestamp_utc"))
    row_age = (now - recorded).total_seconds() if recorded else None
    sensor_age = number(row.get("reading_age_seconds"))
    fresh_row = row_age is not None and -5 <= row_age <= 30
    fresh_sensor = (sensor_age is not None and 0 <= sensor_age <= 20
                    and row.get("sensor_fault") == "0")
    controller = active_service("kiln-controller.service")
    master = active_service("kiln-relay-master.service")
    process = kiln_alive()
    armed = ARM_FILE.is_file()
    lifecycle = read_state()
    previous_boot_interrupted = (
        lifecycle.get("status") == "running"
        and bool(boot_id()) and bool(lifecycle.get("boot_id"))
        and lifecycle["boot_id"] != boot_id()
    )
    # The inactive controller alone is not proof the GPIO outputs were set OFF.
    # Only contact the single-client relay master when the controller is stopped.
    off_confirmed = False
    off_detail = "Not checked while kiln controller may be operating"
    if not controller and not process and not armed:
        if master:
            def safe_to_contact_master():
                return (not active_service("kiln-controller.service")
                        and not kiln_alive()
                        and not ARM_FILE.is_file()
                        and active_service("kiln-relay-master.service"))
            off_confirmed, off_detail = confirm_off(safe_to_contact_master)
        else:
            off_detail = "UNVERIFIED: relay-master service is not active"

    good = controller and master and process and fresh_row and fresh_sensor and armed

    problems = []
    if not controller: problems.append("kiln-controller.service inactive")
    if not master: problems.append("kiln-relay-master.service inactive")
    if not process: problems.append("kiln process absent")
    if not fresh_row: problems.append("CSV measurement missing/stale")
    if not fresh_sensor: problems.append("Bluetooth measurement missing/stale/faulty")

    recent = [r for r in rows
              if (stamp := timestamp(r.get("timestamp_utc")))
              and 0 <= (now - stamp).total_seconds() <= WINDOW]

    def average(column):
        values = [v for r in recent if (v := number(r.get(column))) is not None]
        return sum(values) / len(values) if values else None

    def duty(column):
        values = [r[column] for r in recent if r.get(column) in ("0", "1")]
        return 100.0 * values.count("1") / len(values) if values else None

    published = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    measured = recorded.strftime("%Y-%m-%d %H:%M:%S UTC") if recorded else "None"
    if previous_boot_interrupted and not controller:
        title = "Kiln status — UNEXPECTED REBOOT · MANUAL RESTART REQUIRED"
        heading = ("🚨 POSSIBLE UNEXPECTED POWER LOSS — ALL_OFF CONFIRMED"
                   if off_confirmed else
                   "🚨 POSSIBLE UNEXPECTED POWER LOSS — RELAY STATE UNVERIFIED")
    elif not armed and not controller:
        title = ("Kiln status — OFF VERIFIED · MANUAL START REQUIRED"
                 if off_confirmed else
                 "Kiln status — CONTROLLER DISABLED · MANUAL START REQUIRED · RELAYS UNVERIFIED")
        heading = ("⏸️ CONTROLLER STOPPED — RELAY MASTER CONFIRMS OFF"
                   if off_confirmed else
                   "⚠️ CONTROLLER STOPPED — RELAY OFF NOT VERIFIED")
    elif good:
        title = "Kiln status — RUNNING"
        heading = "✅ RUNNING — telemetry healthy"
    else:
        title = "Kiln status — CHECK KILN"
        heading = "⚠️ CHECK KILN — telemetry or service fault"
    def relay_status(column):
        if off_confirmed:
            return "OFF (confirmed by relay master)"
        return switch(row.get(column), fresh_row)

    # A missing OFF check while heating is not a failed safety check.
    # The independent GPIO owner is only queried when the kiln is stopped.
    if controller or process:
        off_check_status = "Not applicable — kiln is running"
    elif armed:
        off_check_status = "Not checked — kiln may be starting"
    elif off_confirmed:
        off_check_status = "Confirmed OFF — all four relay channels acknowledged"
    else:
        off_check_status = f"OFF not verified — {off_detail}"

    startup_permission = ("Granted for this run — manual start authorised"
                          if armed else
                          "Not granted — manual start required")

    body = f"""# Kiln — live status

## {heading}

**Kiln startup permission:** {startup_permission} (always resets on Pi reboot)

**Relay OFF check (stopped kiln only):** {off_check_status}

**Published:** {published}  
**Latest measurement:** {measured}  
**Measurement age:** {display(row_age, ' seconds', 0)}

| Reading | Value |
|---|---:|
| Kiln temperature | {display(number(row.get('temperature_c')), ' °C', 2)} |
| Relative humidity | {display(number(row.get('humidity_percent')), '%', 2)} |
| Pi CPU temperature | {display(number(row.get('cpu_temperature_c')), ' °C', 1)} |
| Bluetooth reading age | {display(sensor_age, ' seconds', 1)} |
| Heater commanded | **{relay_status('heater_on')}** |
| Humidifier commanded | **{relay_status('humidifier_on')}** |
| Fan commanded | **{relay_status('fan_on')}** |
| Kiln startup permission | {startup_permission} |
| Relay OFF check (stopped kiln only) | {off_check_status} |
| Kiln process supervisor (Linux service) | {'Running' if controller else 'Stopped'} |
| Kiln C++ temperature/humidity controller | {'Running' if process else 'Stopped'} |
| Independent safety controller (relay watchdog) | {'Running' if master else 'Not running'} |
| Sensor fault | {'YES' if row.get('sensor_fault') == '1' else 'NO' if row.get('sensor_fault') == '0' else 'Unknown'} |

### Previous 20 minutes ({len(recent)} readings)

| Measurement | Value |
|---|---:|
| Mean temperature | {display(average('temperature_c'), ' °C', 2)} |
| Mean humidity | {display(average('humidity_percent'), '%', 2)} |
| Heater ON (% of samples) | {display(duty('heater_on'), '%', 1)} |
| Humidifier ON (% of samples) | {display(duty('humidifier_on'), '%', 1)} |
| Fan ON (% of samples) | {display(duty('fan_on'), '%', 1)} |
| Sensor-fault samples | {sum(r.get('sensor_fault') == '1' for r in recent)} |

### Shutdown and restart history

{lifecycle_section()}

**Checks:** {'; '.join(problems) if problems else 'No issues detected'}  
**Source CSV:** \`{path.name if path else 'none'}\`

> **Monitoring only.** A powered-off or disconnected Pi cannot update this Issue; check the published UTC timestamp. Reports more than ~40 minutes old are potentially stale. OFF confirmation means the GPIO-owning relay master acknowledged an explicit ALL_OFF command and reported every configured relay OFF; it does NOT measure physical contacts or voltage. While the kiln runs, relay values come from the CSV's commanded states. The controller service requires an explicit local manual start after any reboot. A power cut cannot be reported until power and internet return. The relay-master watchdog and a physical high-temperature cutoff are still essential.

<!-- KILN_STATUS_REPORT_UTC: {now.strftime('%Y-%m-%dT%H:%M:%SZ')} -->
"""
    return title, body


def publish(title, body, token):
    endpoint = f"https://api.github.com/repos/{REPO}/issues/{ISSUE}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"title": title, "body": body}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "kiln-github-status/1.0"
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)["html_url"]
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub HTTP {exc.code}: {detail}") from exc


def main():
    if not TOKEN_FILE.is_file():
        raise RuntimeError(f"Missing token at {TOKEN_FILE} — see docs/github-status.md")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("GitHub token file is empty")
    title, body = report()
    print("Updated", publish(title, body, token))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print("[KILN MONITOR] " + str(exc), file=sys.stderr)
        sys.exit(1)

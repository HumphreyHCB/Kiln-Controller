#!/usr/bin/env python3
"""Read-only kiln CSV telemetry publisher for GitHub Issue #1.

This program never contacts the kiln relay socket or changes GPIO state.
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

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
TOKEN_FILE = Path.home() / ".config/kiln/github-token"
REPO = "HumphreyHCB/Kiln-Controller"
ISSUE = 1
WINDOW = 20 * 60


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
    good = controller and master and process and fresh_row and fresh_sensor

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
    title = "Kiln status — RUNNING" if good else "Kiln status — CHECK KILN"
    body = f"""# Kiln — live status

## {'✅ RUNNING — telemetry healthy' if good else '⚠️ CHECK KILN — telemetry or service fault'}

**Published:** {published}  
**Latest measurement:** {measured}  
**Measurement age:** {display(row_age, ' seconds', 0)}

| Reading | Value |
|---|---:|
| Kiln temperature | {display(number(row.get('temperature_c')), ' °C', 2)} |
| Relative humidity | {display(number(row.get('humidity_percent')), '%', 2)} |
| Pi CPU temperature | {display(number(row.get('cpu_temperature_c')), ' °C', 1)} |
| Bluetooth reading age | {display(sensor_age, ' seconds', 1)} |
| Heater commanded | **{switch(row.get('heater_on'), fresh_row)}** |
| Humidifier commanded | **{switch(row.get('humidifier_on'), fresh_row)}** |
| Fan commanded | **{switch(row.get('fan_on'), fresh_row)}** |
| Controller systemd service | {'Active' if controller else 'Inactive'} |
| Kiln process | {'Present' if process else 'Absent'} |
| Relay-master service | {'Active' if master else 'Inactive'} |
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

**Checks:** {'; '.join(problems) if problems else 'No issues detected'}  
**Source CSV:** \`{path.name if path else 'none'}\`

> **Monitoring only.** A powered-off or disconnected Pi cannot update this Issue; check the published UTC timestamp. Reports more than ~40 minutes old are potentially stale. Relay values are software-commanded states, not verified electrical contacts. Maintain the independent relay watchdog and a physical high-temperature cutoff.

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

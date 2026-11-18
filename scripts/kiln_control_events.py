#!/usr/bin/env python3
"""Read durable C++ controller incidents for GitHub reporting.

The C++ process writes one JSON object per line; readers ignore partial and
malformed rows. Monitoring never modifies these logs or controls any relay.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS_FILE = ROOT / "logs/kiln_control_events.jsonl"
KNOWN_KINDS = {"heater_timeout", "heater_timeout_shutdown", "sensor_stale"}


def recent_events(limit=100):
    try:
        with EVENTS_FILE.open("rb") as stream:
            stream.seek(0, 2)
            end = stream.tell()
            stream.seek(max(0, end - 256 * 1024))
            if stream.tell():
                stream.readline()  # Discard possibly partial first line
            chunk = stream.read()
    except OSError:
        return []
    result = []
    for line in chunk.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if (isinstance(event, dict)
                and event.get("kind") in KNOWN_KINDS
                and isinstance(event.get("id"), str)
                and isinstance(event.get("detected_utc"), str)
                and isinstance(event.get("detail"), str)):
            result.append(event)
    return result[-limit:]


def issue_history(limit=6):
    labels = {
        "heater_timeout": "⚠️ Heater forced OFF — ON-time limit reached",
        "heater_timeout_shutdown": "🚨 Kiln shut down — repeated heater timeout",
        "sensor_stale": "🚨 Bluetooth sensor reading stale — relays commanded OFF",
    }
    events = recent_events(limit)
    if not events:
        return "No heater-timeout or sensor-staleness events recorded."
    return "\n".join(
        f"- **{labels[e['kind']]}** — {e['detected_utc']}. {e['detail']}"
        for e in reversed(events)
    )

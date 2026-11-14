#!/usr/bin/env python3
"""Persistent controller lifecycle breadcrumbs.

Invoked by systemd ExecStartPre / ExecStopPost. Does not control relays.
A prior 'running' marker from another boot suggests an unclean shutdown.
"""
import csv
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "logs/kiln_controller_lifecycle.json"
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")
UTC = dt.timezone.utc


def now_utc():
    return dt.datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def boot_id():
    try:
        return BOOT_ID_FILE.read_text(encoding="ascii").strip()
    except OSError:
        return None


def read_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def write_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace plus fsync reduces risk of losing the marker to sudden power loss.
    tmpname = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=STATE_FILE.parent,
            prefix=".kiln-lifecycle-", delete=False
        ) as stream:
            tmpname = stream.name
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmpname, STATE_FILE)
        tmpname = None
        directory_fd = os.open(STATE_FILE.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if tmpname:
            Path(tmpname).unlink(missing_ok=True)


def last_csv_measurement():
    try:
        files = sorted((ROOT / "logs").glob("kiln_*.csv"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[:3]:
            with path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                end = f.tell()
                f.seek(max(0, end - 16384))
                chunk = f.read().decode("utf-8", errors="replace")
            for line in reversed(chunk.splitlines()):
                if line.startswith("20") and "," in line:
                    return next(csv.reader([line]))[0]
    except (OSError, ValueError, csv.Error, IndexError):
        pass
    return None


def add_event(state, event):
    events = state.get("events", [])
    if not isinstance(events, list):
        events = []
    state["events"] = (events + [event])[-10:]


def mark_start():
    state = read_state()
    current = boot_id()
    time = now_utc()
    previous = state.get("status")
    if previous == "running":
        changed_boot = bool(current and state.get("boot_id")
                            and state.get("boot_id") != current)
        add_event(state, {
            "kind": "suspected_unclean_reboot" if changed_boot
                    else "previous_run_not_cleanly_closed",
            "detected_utc": time,
            "previous_started_utc": state.get("started_utc"),
            "last_measurement_before_restart_utc": last_csv_measurement(),
            "detail": "Prior run was marked active, without a recorded clean stop",
        })
    state.update({
        "version": 1, "status": "running", "boot_id": current,
        "started_utc": time, "last_change_utc": time,
    })
    write_state(state)
    print("[KILN LIFECYCLE] Controller start recorded.", flush=True)


def mark_stop():
    state = read_state()
    # ExecStopPost may run on the next boot even when ExecCondition skipped
    # startup. Never overwrite the prior boot's running marker: it provides
    # evidence that the old run ended without a recorded clean stop.
    if (state.get("status") != "running"
            or state.get("boot_id") != boot_id()):
        return

    time = now_utc()
    result = os.environ.get("SERVICE_RESULT", "unknown")
    exit_code = os.environ.get("EXIT_CODE", "unknown")
    exit_status = os.environ.get("EXIT_STATUS", "unknown")
    # The supervisor exits 130 when it handles a deliberate systemd stop.
    clean = result == "success" or (exit_code == "exited" and exit_status == "130")
    if not clean:
        add_event(state, {
            "kind": "controller_service_failure",
            "detected_utc": time,
            "detail": f"systemd: {result}; exit: {exit_code} {exit_status}",
        })
    state.update({
        "status": "stopped" if clean else "failed",
        "last_change_utc": time, "last_service_result": result,
        "last_exit_code": exit_code, "last_exit_status": exit_status,
    })
    write_state(state)
    print(f"[KILN LIFECYCLE] Controller stop recorded ({result}).", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("start", "stop"):
        sys.exit("Usage: kiln_service_state.py start|stop")
    if sys.argv[1] == "start":
        mark_start()
    else:
        mark_stop()

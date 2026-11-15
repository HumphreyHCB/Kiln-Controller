#!/usr/bin/env python3
"""Event-only GitHub alerts for the kiln status Issue.

The normal 20-minute body PATCH is silent. Only significant new events become
issue comments; a tightly scoped GitHub Actions workflow mentions the owner
from github-actions[bot] so notifications are not self-authored.

No GPIO or kiln control operations take place here.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request
import uuid

from kiln_service_state import boot_id, read_state, last_csv_measurement

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "logs/kiln_github_notification_state.json"
REPO = "HumphreyHCB/Kiln-Controller"
ISSUE = 1
MARKER_PREFIX = "<!-- KILN_EVENT_NOTIFY:"
USER_AGENT = "kiln-github-event-notifier/1.0"
UTC = dt.timezone.utc


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def save_state(state):
    """Persist event checkpoints so timer runs and Pi reboots do not re-alert."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=STATE_FILE.parent,
            prefix=".kiln-github-alert-", delete=False
        ) as stream:
            tmp = stream.name
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, STATE_FILE)
        tmp = None
        fd = os.open(STATE_FILE.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def event_key(event):
    canonical = json.dumps(event, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def queue(state, event_id, text):
    sent = state.setdefault("sent", [])
    pending = state.setdefault("pending", [])
    if event_id not in sent and not any(e["id"] == event_id for e in pending):
        pending.append({"id": event_id, "body": text})
        return True
    return False


def github_request(token, method, suffix, data=None):
    endpoint = f"https://api.github.com/repos/{REPO}/issues/{ISSUE}{suffix}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        endpoint, method=method, headers=headers,
        data=(json.dumps(data).encode("utf-8") if data is not None else None),
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(256).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub alert API HTTP {exc.code}: {detail}") from exc


def publish_notifications(state, token):
    """Check GitHub comments before posting to avoid double alerts on retry."""
    if not state.get("pending"):
        return
    comments = github_request(token, "GET", "/comments?per_page=100&sort=created&direction=desc")
    known = {c.get("body", "") for c in comments}
    while state["pending"]:
        event = state["pending"][0]
        marker = f"{MARKER_PREFIX}{event['id']} -->"
        body = event["body"] + "\n\n" + marker
        if not any(marker in existing for existing in known):
            github_request(token, "POST", "/comments", {"body": body})
        state["sent"] = (state.get("sent", []) + [event["id"]])[-200:]
        state["pending"].pop(0)
        save_state(state)
        known.add(body)


def notify(title, issue_body, token, *, now=None):
    """Publish only newly detected starts, faults and unclean interruptions.

    Call AFTER the 20-minute Issue body has been updated successfully.
    Does not affect control decisions, relay state, or Issue title.
    """
    now = now or dt.datetime.now(UTC)
    state = load_state()
    lifecycle = read_state()
    current_boot = boot_id()
    prior_boot = lifecycle.get("boot_id")
    current_run = (
        f"{prior_boot}:{lifecycle.get('started_utc')}"
        if lifecycle.get("status") == "running" and prior_boot
        and lifecycle.get("started_utc") else None
    )
    healthy = title == "Kiln status — RUNNING"
    unclean = title.startswith("Kiln status — UNEXPECTED REBOOT")
    fault = (title.startswith("Kiln status — CHECK KILN")
             or "RELAYS UNVERIFIED" in title)

    # On initial deployment, a run already in progress is not a "new start".
    # Existing old service-failure history is likewise treated as baseline.
    if not state.get("initialized"):
        state = {
            "initialized": True, "sent": [], "pending": [],
            "last_successful_run": current_run,
            "last_fault_active": False,
            "seen_lifecycle": [
                event_key(e) for e in lifecycle.get("events", [])
                if isinstance(e, dict)
            ],
        }

    # A new completed start is reported ONCE, not every time a fresh CSV
    # is written, not on each heater relay cycle, and not after a mere
    # recovery from sensor staleness within the same service start.
    if healthy and current_run and current_run != state.get("last_successful_run"):
        queue(state, "started:" + event_key({"run": current_run}),
              "### ✅ Kiln controller successfully started\n\n"
              f"**Verified:** {now.isoformat(timespec='seconds')}  \n"
              "**Status:** The managed C++ controller and GPIO relay watchdog "
              "are active, and fresh Bluetooth telemetry is being received.\n\n"
              "No action is needed while normal operation continues.")
        state["last_successful_run"] = current_run

    # Preserve alert about a power cut across reboots, but notify only once
    # for the specific old/new boot-ID pair.
    if unclean and current_boot and prior_boot and prior_boot != current_boot:
        queue(state, "unclean-run:" + event_key({"started": lifecycle.get("started_utc") or prior_boot}),
              "### 🚨 Possible unexpected power loss / system reboot\n\n"
              f"**Detected:** {now.isoformat(timespec='seconds')}  \n"
              "**Previous controller state:** Running without a clean stop record  \n"
              f"**Last CSV measurement:** {last_csv_measurement() or 'Unavailable'}  \n"
              "**Current controller:** Not started; manual intervention required.  \n"
              "**Relay outputs:** Check the live Issue's explicit OFF verification. "
              "Do not assume physical contacts are isolated.\n\n"
              "The Pi cannot identify the precise cause or instant of the outage.")

    # Service failure records survive even if the controller stops between
    # two twenty-minute status samples.
    seen = state.setdefault("seen_lifecycle", [])
    for event in lifecycle.get("events", []):
        if not isinstance(event, dict):
            continue
        event_id = event_key(event)
        if event_id in seen:
            continue
        seen.append(event_id)
        kind = event.get("kind")
        if kind == "controller_service_failure":
            queue(state, "service-failed:" + event_id,
                  "### 🚨 Kiln controller stopped unexpectedly\n\n"
                  f"**Detected:** {event.get('detected_utc', 'Unknown')}  \n"
                  f"**Service result:** {event.get('detail', 'Unavailable')}  \n"
                  "**Action:** Check the controller and independent relay watchdog. "
                  "The kiln will not restart automatically.")
        elif kind in ("suspected_unclean_reboot", "previous_run_not_cleanly_closed"):
            queue(state, "unclean-run:" + event_key({"started": event.get("previous_started_utc") or event_id}),
                  "### 🚨 Previous kiln run ended without a clean stop\n\n"
                  f"**Detected:** {event.get('detected_utc', 'Unknown')}  \n"
                  f"**Last measurement:** {event.get('last_measurement_before_restart_utc') or 'Unknown'}  \n"
                  "**Action:** Verify outputs OFF and inspect the kiln before restarting.")
    state["seen_lifecycle"] = seen[-200:]

    # Warn on first observed fault, not on every subsequent reading.
    # When healthy or intentionally stopped again, re-arm for a future fault.
    if fault and not state.get("last_fault_active") and not unclean:
        match = re.search(r"\*\*Checks:\*\* ([^\n]+)", issue_body)
        problems = match.group(1).strip() if match else "See current Issue"
        queue(state, "fault:" + uuid.uuid4().hex,
              "### 🚨 Kiln requires attention\n\n"
              f"**Detected:** {now.isoformat(timespec='seconds')}  \n"
              f"**Reported problems:** {problems}\n\n"
              "Review the current live Issue and the Pi service logs.")
    state["last_fault_active"] = bool(fault)

    # Save pending alerts before contacting GitHub so failed network calls
    # can be retried at the next timer tick.
    save_state(state)
    publish_notifications(state, token)

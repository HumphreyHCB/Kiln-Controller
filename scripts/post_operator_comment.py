#!/usr/bin/env python3
"""Post an operator start/stop acknowledgement as a GitHub Issue comment.

Called by start/stop scripts after a successful operation as the 'humphrey'
user (which owns the existing fine-grained GitHub token). These comments
are records, not emergency alerts, and carry no notification workflow marker.
"""
import datetime as dt
import json
from pathlib import Path
import sys
import urllib.request

TOKEN_FILE = Path.home() / ".config/kiln/github-token"
URL = "https://api.github.com/repos/HumphreyHCB/Kiln-Controller/issues/1/comments"

MESSAGES = {
    "start": ("### ▶️ Manual kiln start completed\n\n"
              "The operator successfully completed a full clean build and "
              "manually started the kiln controller. Live telemetry may "
              "still be initializing."),
    "stop": ("### ⏹️ Manual kiln stop completed\n\n"
             "The operator stopped the kiln controller and the independent "
             "relay master acknowledged an explicit ALL_OFF command. "
             "This does not independently measure physical relay contacts."),
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in MESSAGES:
        raise ValueError("Expected exactly one internal operation: start|stop")
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("GitHub token empty")
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    body = MESSAGES[sys.argv[1]] + f"\n\n**Time:** {now}"
    request = urllib.request.Request(
        URL, method="POST", data=json.dumps({"body": body}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kiln-operator-action/1.0",
        })
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)
    print("[KILN OPERATOR] Commented on Issue #1:", data["html_url"], flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print("[KILN OPERATOR] GitHub comment failed:", error, file=sys.stderr)
        sys.exit(1)

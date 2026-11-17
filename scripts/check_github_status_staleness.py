#!/usr/bin/env python3
"""Off-Pi GitHub Actions heartbeat check for Kiln status Issue #1.

Unlike the Pi's status publisher, this runs on GitHub. It uses the timestamp
inside the Issue BODY, since a comment also updates GitHub's updated_at field.
It does not control relays. Each missing heartbeat gets at most one alert.
"""
import datetime as dt
import json
import os
import re
import urllib.request

REPO = "HumphreyHCB/Kiln-Controller"
ISSUE = 1
MAX_AGE = dt.timedelta(minutes=45)
UTC = dt.timezone.utc
PATTERN = re.compile(r"<!-- KILN_STATUS_REPORT_UTC: (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) -->")
ALERT_MARKER = "<!-- KILN_HEARTBEAT_STALE:"
USER_AGENT = "kiln-github-external-heartbeat/1.0"


def heartbeat(body):
    match = PATTERN.search(body or "")
    if not match:
        return None
    try:
        return dt.datetime.fromisoformat(
            match.group(1).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def alert_needed(body, now, comment_bodies):
    last = heartbeat(body)
    if last is not None and dt.timedelta(0) <= now - last <= MAX_AGE:
        return None
    if last is not None and last - now <= dt.timedelta(minutes=5) and now < last:
        return None  # Small clock skew is not a missing heartbeat.
    stamp = last.strftime("%Y-%m-%dT%H:%M:%SZ") if last else "unknown"
    marker = f"{ALERT_MARKER}{stamp} -->"
    if any(marker in text for text in comment_bodies):
        return None
    age = (f"{int((now - last).total_seconds() // 60)} minutes"
           if last and now >= last else
           "invalid future timestamp (check Pi clock)"
           if last else "unknown (heartbeat marker missing)")
    return (
        "@HumphreyHCB 🚨 **Kiln GitHub status is stale.**\n\n"
        f"Last confirmed report: **{stamp}**  \n"
        f"Time since last report: **{age}**  \n\n"
        "The Pi may have lost power, internet, or its monitoring timer. "
        "The Issue's RUNNING label does **not** prove current operation. "
        "Check the kiln locally and verify heater safety. This alert is "
        "generated externally by GitHub Actions.\n\n"
        + marker
    )


def github_get(path, token):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        })
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def github_post(path, data, token):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}{path}",
        data=json.dumps(data).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        })
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main():
    token = os.environ["KILN_GITHUB_TOKEN"]
    issue = github_get(f"/issues/{ISSUE}", token)
    now = dt.datetime.now(UTC)
    if (last := heartbeat(issue.get("body"))) is not None and dt.timedelta(0) <= now-last <= MAX_AGE:
        print("Kiln Issue heartbeat is fresh:", last.isoformat())
        return

    # The marker prevents repeated alerts every scheduled run. Scan all
    # comments so the warning remains deduplicated even as comments grow.
    comments = []
    page = 1
    while True:
        batch = github_get(
            f"/issues/{ISSUE}/comments?per_page=100&page={page}", token)
        comments.extend(c.get("body", "") for c in batch)
        if len(batch) < 100:
            break
        page += 1
    body = alert_needed(issue.get("body"), now, comments)
    if body:
        github_post(f"/issues/{ISSUE}/comments", {"body": body}, token)
        print("Posted stale status alert for", last)
    else:
        print("No new alert required")


if __name__ == "__main__":
    main()

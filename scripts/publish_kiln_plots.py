#!/usr/bin/env python3
"""Render eight kiln plots and commit them atomically to GitHub main.

The Pi's own Git checkout is NEVER committed/pulled/pushed/modified.
PNG files render into ignored logs/plot-cache and the GitHub Git Data API
publishes them in ONE remote commit per run. No CSV files, secrets, config,
code or other local files are ever uploaded.

The existing fine-grained PAT needs Contents: Read and write in addition
to its existing Issues: Read and write permission.
"""
import argparse
import base64
import datetime as dt
import fcntl
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

from generate_kiln_plots import OUTPUT, ROOT, generate

UTC = dt.timezone.utc
REPOSITORY = "HumphreyHCB/Kiln-Controller"
BASE_URL = f"https://api.github.com/repos/{REPOSITORY}"
BRANCH = "main"
TOKEN_FILE = Path.home() / ".config/kiln/github-token"
DESTINATION = "docs/plots"
LOCK_FILE = ROOT / "logs/.kiln-plot-publisher.lock"


def request_json(method, path, token, data=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "kiln-plot-publisher/1.0",
    }
    payload = None
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, payload, headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        message = exc.read(300).decode("utf-8", errors="replace")
        if exc.code in (401, 403):
            raise RuntimeError(
                f"GitHub HTTP {exc.code}: check Pi GitHub token has "
                "'Contents: Read and write' permission. " + message) from exc
        raise RuntimeError(f"GitHub HTTP {exc.code}: {message}") from exc


def create_plot_blobs(files, token):
    """Upload only approved generated PNG/JSON files as Git blobs."""
    accepted = {
        f"{kind}_{span}.png"
        for kind in ("temperature", "humidity", "pi_cpu", "relays")
        for span in ("24h", "7d")
    } | {"last_updated.json"}
    if {path.name for path in files} != accepted:
        raise ValueError("Unexpected files; refusing to publish")
    entries = []
    for path in sorted(files):
        path = Path(path)
        if path.name.endswith(".png"):
            payload = {
                "content": base64.b64encode(path.read_bytes()).decode("ascii"),
                "encoding": "base64",
            }
        else:
            payload = {"content": path.read_text(encoding="utf-8"),
                       "encoding": "utf-8"}
        blob = request_json("POST", "/git/blobs", token, payload)
        entries.append({
            "path": f"{DESTINATION}/{path.name}",
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
    return entries


def commit_plots(entries, token, stamp, attempts=3):
    """Optimistically update main; retry harmless concurrent ref advances."""
    for attempt in range(1, attempts + 1):
        ref = request_json("GET", f"/git/ref/heads/{BRANCH}", token)
        parent = ref["object"]["sha"]
        base = request_json("GET", f"/git/commits/{parent}", token)["tree"]["sha"]
        tree = request_json("POST", "/git/trees", token,
                            {"base_tree": base, "tree": entries})
        commit = request_json("POST", "/git/commits", token, {
            "message": f"Update kiln plots — {stamp}",
            "tree": tree["sha"],
            "parents": [parent],
        })
        try:
            request_json("PATCH", f"/git/refs/heads/{BRANCH}", token,
                         {"sha": commit["sha"], "force": False})
            return commit["sha"]
        except RuntimeError as exc:
            if ("HTTP 409" not in str(exc) and "HTTP 422" not in str(exc)
                    or attempt == attempts):
                raise
            print("[KILN PLOTS] Main changed during publish; retrying")
    raise RuntimeError("Could not update GitHub after retries")


def run(token, *, render_only=False):
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a", encoding="utf-8") as lock:
        # Systemd already serializes its own service but this also protects
        # against someone manually invoking the script simultaneously.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        files, metadata = generate()
        stamp = metadata["generated_at_utc"]
        print(f"[KILN PLOTS] Generated eight PNG plots at {stamp}", flush=True)
        if render_only:
            print(f"[KILN PLOTS] Render-only: images in {OUTPUT}")
            return None
        entries = create_plot_blobs(files, token)
        commit = commit_plots(entries, token, stamp)
        print("[KILN PLOTS] Committed 8 PNGs and last_updated.json to main:",
              f"https://github.com/{REPOSITORY}/commit/{commit}", flush=True)
        return commit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-only", action="store_true",
                        help="Generate plots locally but do not access GitHub")
    args = parser.parse_args()

    token = ""
    if not args.render_only:
        if not TOKEN_FILE.is_file():
            raise RuntimeError(f"Missing GitHub PAT at {TOKEN_FILE}")
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("Empty GitHub PAT")
    run(token, render_only=args.render_only)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, BlockingIOError) as exc:
        print("[KILN PLOTS] ERROR:", exc, file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Generate eight bounded 24-hour / 7-day PNG plots from existing kiln CSVs.

Read-only with respect to kiln control, relays and logs. Only the fixed output
images and metadata under docs/plots/ are overwritten. Sensor-stale values are
not drawn as valid kiln temperature/humidity; missing CSV intervals stay gaps.

The current CSV schema already includes all needed fields. No C++ changes.
"""
import argparse
import csv
from dataclasses import dataclass, field
import datetime as dt
import json
import math
from pathlib import Path
from typing import Optional

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"
OUTPUT = ROOT / "docs/plots"
CONFIG = ROOT / "config/kiln-control.json"
RELAYS = ("heater_on", "humidifier_on", "fan_on")
WINDOWS = {"24h": (24 * 3600, 120), "7d": (7 * 86400, 900)}
PLOT_NAMES = ("temperature", "humidity", "pi_cpu", "relays")


def parse_time(text):
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return value.astimezone(UTC) if value.tzinfo else None
    except (AttributeError, ValueError):
        return None


def valid_number(text, low, high):
    try:
        value = float(text)
        return value if math.isfinite(value) and low <= value <= high else None
    except (ValueError, TypeError):
        return None


@dataclass
class Bucket:
    rows: int = 0
    temperature_sum: float = 0.0
    temperature_count: int = 0
    humidity_sum: float = 0.0
    humidity_count: int = 0
    cpu_sum: float = 0.0
    cpu_count: int = 0
    relay_on: dict = field(default_factory=lambda: {k: 0 for k in RELAYS})
    relay_count: dict = field(default_factory=lambda: {k: 0 for k in RELAYS})

    def observe(self, record):
        self.rows += 1
        cpu = valid_number(record.get("cpu_temperature_c"), -20, 125)
        if cpu is not None:
            self.cpu_sum += cpu
            self.cpu_count += 1

        # Cached/stale sensor data is NOT a fresh kiln reading.
        age = valid_number(record.get("reading_age_seconds"), 0, 20)
        if record.get("sensor_fault") == "0" and age is not None:
            temperature = valid_number(record.get("temperature_c"), -30, 100)
            humidity = valid_number(record.get("humidity_percent"), 0, 100)
            if temperature is not None:
                self.temperature_sum += temperature
                self.temperature_count += 1
            if humidity is not None:
                self.humidity_sum += humidity
                self.humidity_count += 1

        for relay in RELAYS:
            if record.get(relay) in ("0", "1"):
                self.relay_count[relay] += 1
                self.relay_on[relay] += int(record[relay])

    @staticmethod
    def mean(total, count):
        return total / count if count else float("nan")

    def value(self, name):
        if name == "temperature":
            return self.mean(self.temperature_sum, self.temperature_count)
        if name == "humidity":
            return self.mean(self.humidity_sum, self.humidity_count)
        if name == "pi_cpu":
            return self.mean(self.cpu_sum, self.cpu_count)
        if name == "pi_logging":
            return float(self.rows > 0)
        if name in RELAYS:
            return self.mean(self.relay_on[name], self.relay_count[name])
        raise KeyError(name)


@dataclass
class Window:
    name: str
    end: dt.datetime
    duration_seconds: int
    bin_seconds: int
    bins: list = field(init=False)
    records: int = 0

    def __post_init__(self):
        self.start = self.end - dt.timedelta(seconds=self.duration_seconds)
        self.bins = [Bucket() for _ in
                     range(self.duration_seconds // self.bin_seconds)]

    def observe(self, instant, record):
        if instant < self.start or instant > self.end:
            return
        offset = (instant - self.start).total_seconds()
        index = min(int(offset // self.bin_seconds), len(self.bins) - 1)
        self.bins[index].observe(record)
        self.records += 1

    def series(self, column):
        return [bucket.value(column) for bucket in self.bins]

    def midpoints(self):
        return [
            self.start + dt.timedelta(seconds=(i + 0.5) * self.bin_seconds)
            for i in range(len(self.bins))
        ]


def collect(log_dir=LOGS, now=None):
    now = now or dt.datetime.now(UTC)
    windows = {name: Window(name, now, *parameters)
               for name, parameters in WINDOWS.items()}
    earliest = windows["7d"].start

    for path in sorted(Path(log_dir).glob("kiln_*.csv")):
        try:
            # Old closed CSVs cannot contain new measurements. The extra day
            # allows for clock/mtime skew and recent file copies.
            if path.stat().st_mtime < earliest.timestamp() - 86400:
                continue
            with path.open("r", newline="", encoding="utf-8") as stream:
                for record in csv.DictReader(stream):
                    when = parse_time(record.get("timestamp_utc"))
                    if when is None or when < earliest or when > now:
                        continue
                    for window in windows.values():
                        window.observe(when, record)
        except (OSError, UnicodeError, csv.Error):
            # One truncated/temporarily inaccessible CSV must not prevent
            # plotting the other runs. Partial last lines are simply skipped.
            continue
    return windows


def read_current_targets(path=CONFIG):
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            "temperature_min": float(config["temperature"]["min"]),
            "temperature_off": (
                float(config["temperature"]["max"]) -
                float(config["temperature"]["stopBeforeMax"])),
            "humidity_min": float(config["humidity"]["min"]),
            "humidity_off": (
                float(config["humidity"]["max"]) -
                float(config["humidity"]["stopBeforeMax"])),
        }
    except (OSError, KeyError, TypeError, ValueError):
        return {}


def draw(window, output_dir=OUTPUT, targets=None):
    # Lazy import: aggregation and error handling can be tested without GUI
    # libraries; the Pi uses Debian's system python3-matplotlib (Agg).
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import numpy as np

    targets = targets or {}
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    start_num = mdates.date2num(window.start)
    end_num = mdates.date2num(window.end)
    x = window.midpoints()
    generated = []

    for column, title, unit, target_keys in (
        ("temperature", "Kiln temperature", "°C",
         ("temperature_min", "temperature_off")),
        ("humidity", "Kiln relative humidity", "% RH",
         ("humidity_min", "humidity_off")),
        ("pi_cpu", "Raspberry Pi CPU temperature", "°C", ()),
    ):
        fig, ax = plt.subplots(figsize=(14, 4.4), layout="constrained")
        values = window.series(column)
        ax.plot(x, values, linewidth=1.4, label=title)
        if target_keys and all(k in targets for k in target_keys):
            low, upper = (targets[k] for k in target_keys)
            if math.isfinite(low) and math.isfinite(upper) and low < upper:
                ax.axhspan(low, upper, alpha=0.08, label="Current control band")
                ax.axhline(low, linestyle="--", linewidth=0.8, alpha=0.6)
                ax.axhline(upper, linestyle="--", linewidth=0.8, alpha=0.6)
        if not any(math.isfinite(v) for v in values):
            ax.text(0.5, 0.5, "No fresh measurements in this window",
                    ha="center", va="center", transform=ax.transAxes)
        ax.set_title(f"{title} — last {window.name}")
        ax.set_ylabel(unit)
        ax.set_xlim(window.start, window.end)
        ax.grid(True, alpha=0.25)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=11))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M", tz=UTC))
        ax.tick_params(axis="x", labelrotation=20)
        ax.set_xlabel("UTC • gaps mean no fresh sensor/log data (not zero)")
        if column != "pi_cpu" and target_keys:
            ax.legend(loc="upper right", fontsize=8)
        filename = f"{column}_{window.name}.png"
        fig.savefig(destination / filename, dpi=125, format="png")
        plt.close(fig)
        generated.append(destination / filename)

    # A compact state-timeline. 24h = 2-minute, 7d = 15-minute bins.
    # Relay rows = fraction of COMMANDS reporting ON within that bucket.
    # Missing buckets are grey/unknown, NOT interpreted as relay OFF.
    # Pi row is CSV activity observed/not observed; absence is not proof off.
    import matplotlib.colors as colors
    timeline = np.asarray([
        window.series("pi_logging"),
        window.series("heater_on"),
        window.series("humidifier_on"),
        window.series("fan_on"),
    ], dtype=float)
    mask = np.ma.masked_invalid(timeline)
    color_map = plt.get_cmap("viridis").copy()
    color_map.set_bad("#dddddd")
    fig, ax = plt.subplots(figsize=(14, 4.4), layout="constrained")
    image = ax.imshow(
        mask, cmap=color_map, vmin=0, vmax=1, aspect="auto",
        interpolation="nearest",
        extent=[start_num, end_num, 3.5, -0.5],
    )
    ax.set_yticks([0, 1, 2, 3],
                  ["Pi logging observed", "Heater", "Humidifier", "Fan"])
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=11))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M", tz=UTC))
    ax.tick_params(axis="x", labelrotation=20)
    ax.set_title(f"Kiln relay activity and Pi logging — last {window.name}")
    ax.set_xlabel("UTC • grey = no relay data; missing Pi logs ≠ verified power OFF")
    bar = fig.colorbar(image, ax=ax, fraction=0.023, pad=0.012)
    bar.set_label("Logged ON fraction (Pi: data present)")
    filename = f"relays_{window.name}.png"
    fig.savefig(destination / filename, dpi=125, format="png")
    plt.close(fig)
    generated.append(destination / filename)
    return generated


def generate(log_dir=LOGS, output_dir=OUTPUT, now=None):
    now = now or dt.datetime.now(UTC)
    windows = collect(log_dir, now)
    targets = read_current_targets()
    files = []
    for name in WINDOWS:
        files.extend(draw(windows[name], output_dir, targets))
    metadata = {
        "generated_at_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "windows": {
            name: {
                "start_utc": w.start.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "end_utc": w.end.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "csv_rows": w.records,
                "bins_with_logs": sum(bool(b.rows) for b in w.bins),
                "total_bins": len(w.bins),
            }
            for name, w in windows.items()
        },
        "note": ("Pi activity inferred from CSV records; missing rows cannot "
                 "prove Pi power OFF. Relay states are software commands."),
    }
    metadata_path = Path(output_dir) / "last_updated.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n",
                             encoding="utf-8")
    files.append(metadata_path)
    return files, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=LOGS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    files, metadata = generate(args.logs, args.output)
    print("[KILN PLOTS] Generated", len(files) - 1, "PNG plots from",
          metadata["windows"]["7d"]["csv_rows"], "recent CSV rows:")
    for path in files:
        print(" ", path)


if __name__ == "__main__":
    main()

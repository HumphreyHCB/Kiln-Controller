"""Unit and rendering tests for bounded, automated GitHub kiln plots."""
import csv
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_kiln_plots as charts
import publish_kiln_plots as publisher

UTC = dt.timezone.utc


def write_rows(directory, rows):
    destination = Path(directory) / "kiln_test.csv"
    columns = [
        "timestamp_utc", "temperature_c", "humidity_percent",
        "reading_age_seconds", "heater_on", "humidifier_on",
        "fan_on", "sensor_fault", "cpu_temperature_c",
    ]
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def row(when, temp=44.0, humidity=72.0, sensor_age=1.0,
        heater=1, humidifier=0, fan=1, fault=0, cpu=58.0):
    return {
        "timestamp_utc": when.isoformat().replace("+00:00", "Z"),
        "temperature_c": temp, "humidity_percent": humidity,
        "reading_age_seconds": sensor_age, "heater_on": heater,
        "humidifier_on": humidifier, "fan_on": fan,
        "sensor_fault": fault, "cpu_temperature_c": cpu,
    }


class KilnPlotsTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.now(UTC).replace(microsecond=0)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logs = Path(self.temp.name) / "logs"
        self.logs.mkdir()
        self.output = Path(self.temp.name) / "png"

    def test_window_limits_and_stale_sensor_values_excluded(self):
        write_rows(self.logs, [
            row(self.now - dt.timedelta(hours=23), temp=43, humidity=71),
            row(self.now - dt.timedelta(hours=2), temp=45, humidity=73,
                sensor_age=45, fault=1, cpu=70),
            row(self.now - dt.timedelta(hours=2, minutes=-2), temp=46,
                humidity=74, heater=0),
            row(self.now - dt.timedelta(days=3), temp=42),
            row(self.now - dt.timedelta(days=8), temp=41),
            row(self.now + dt.timedelta(minutes=10), temp=66),
            {"timestamp_utc": "corrupt", "temperature_c": "1000"},
        ])
        windows = charts.collect(self.logs, self.now)
        self.assertEqual(windows["24h"].records, 3)
        self.assertEqual(windows["7d"].records, 4)

        two_hours = self.now - dt.timedelta(hours=2)
        w = windows["24h"]
        idx = int((two_hours - w.start).total_seconds() // w.bin_seconds)
        self.assertEqual(w.bins[idx].rows, 1)
        self.assertEqual(w.bins[idx].temperature_count, 0)
        self.assertEqual(w.bins[idx].humidity_count, 0)
        self.assertEqual(w.bins[idx].cpu_count, 1)
        self.assertEqual(w.bins[idx].value("heater_on"), 1.0)
        self.assertEqual(w.bins[idx].value("pi_logging"), 1.0)
        self.assertEqual(w.bins[0].value("pi_logging"), 0.0)
        self.assertTrue(w.bins[0].value("heater_on") !=
                        w.bins[0].value("heater_on"))  # Unknown, not OFF

    def test_missing_data_does_not_create_fake_zero_temperature(self):
        windows = charts.collect(self.logs, self.now)
        self.assertEqual(windows["24h"].records, 0)
        self.assertTrue(all(bucket.rows == 0
                            for bucket in windows["7d"].bins))
        self.assertTrue(all(v != v
                            for v in windows["24h"].series("temperature")))

    def test_current_setpoints_loaded_without_mutating_config(self):
        path = self.logs / "config.json"
        path.write_text(json.dumps({
            "temperature": {"min": 40.5, "max": 48,
                            "stopBeforeMax": 1.5},
            "humidity": {"min": 70, "max": 78,
                         "stopBeforeMax": 5},
        }))
        current = charts.read_current_targets(path)
        self.assertEqual(current["temperature_min"], 40.5)
        self.assertEqual(current["temperature_off"], 46.5)
        self.assertEqual(current["humidity_off"], 73.0)

    def test_render_exactly_eight_pngs_and_metadata(self):
        # CI installs matplotlib for this real rendering check.
        import matplotlib
        matplotlib.use("Agg")
        write_rows(self.logs, [
            row(self.now - dt.timedelta(minutes=5)),
            row(self.now - dt.timedelta(hours=15), heater=0),
        ])
        files, meta = charts.generate(self.logs, self.output, self.now)
        self.assertEqual(len(files), 9)
        for key in charts.PLOT_NAMES:
            for period in charts.WINDOWS:
                image = self.output / f"{key}_{period}.png"
                self.assertTrue(image.exists())
                self.assertEqual(image.read_bytes()[:8],
                                 b"\x89PNG\r\n\x1a\n")
        self.assertEqual(meta["windows"]["24h"]["csv_rows"], 2)
        self.assertIn("missing rows cannot prove Pi power OFF",
                      meta["note"])
        self.assertEqual(len(list(self.output.glob("*.png"))), 8)

    def test_publisher_only_uploads_exact_fixed_plot_names(self):
        self.output.mkdir()
        names = [f"{kind}_{span}.png"
                 for kind in charts.PLOT_NAMES for span in charts.WINDOWS]
        files = []
        for name in names:
            path = self.output / name
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fixture")
            files.append(path)
        meta = self.output / "last_updated.json"
        meta.write_text('{"generated_at_utc":"2026-11-17T12:00:00Z"}')
        files.append(meta)

        calls = []
        def fake_request(method, path, token, data=None):
            calls.append((method, path, data))
            return {"sha": f"blob{len(calls)}"}

        with patch.object(publisher, "request_json",
                          side_effect=fake_request):
            entries = publisher.create_plot_blobs(files, "fake-token")
        self.assertEqual(len(calls), 9)
        self.assertEqual({e["path"] for e in entries},
                         {"docs/plots/" + name for name in names + [meta.name]})
        self.assertTrue(all(e["type"] == "blob" for e in entries))

        with self.assertRaises(ValueError):
            publisher.create_plot_blobs(files[:-1], "fake-token")

    def test_publisher_one_remote_commit_and_fast_forward(self):
        entries = [{"path": "docs/plots/temperature_24h.png",
                    "sha": "abc", "mode": "100644", "type": "blob"}]
        calls = []
        def fake_request(method, path, token, data=None):
            calls.append((method, path, data))
            if path == "/git/ref/heads/main" and method == "GET":
                return {"object": {"sha": "oldhead"}}
            if path == "/git/commits/oldhead":
                return {"tree": {"sha": "oldtree"}}
            if path == "/git/trees":
                self.assertEqual(data["base_tree"], "oldtree")
                self.assertEqual(data["tree"], entries)
                return {"sha": "newtree"}
            if path == "/git/commits":
                self.assertEqual(data["parents"], ["oldhead"])
                return {"sha": "newhead"}
            if method == "PATCH":
                self.assertEqual(data, {"sha": "newhead", "force": False})
                return {"object": {"sha": "newhead"}}
            raise AssertionError((method, path))
        with patch.object(publisher, "request_json",
                          side_effect=fake_request):
            self.assertEqual(
                publisher.commit_plots(entries, "token", "2026-11-17T12:00:00Z"),
                "newhead")
        self.assertEqual([x[0] for x in calls],
                         ["GET", "GET", "POST", "POST", "PATCH"])


if __name__ == "__main__":
    unittest.main()

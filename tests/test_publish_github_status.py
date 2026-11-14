import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts/publish_github_status.py"
spec = importlib.util.spec_from_file_location("kiln_status", MODULE)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def test_missing_csv_reports_problem(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(monitor, "LOGS", Path(temp)), \
             patch.object(monitor, "active_service", return_value=True), \
             patch.object(monitor, "kiln_alive", return_value=True):
            title, body = monitor.report(dt.datetime(2026,11,14,12,tzinfo=dt.timezone.utc))
            self.assertIn("CHECK KILN", title)
            self.assertIn("CSV measurement missing/stale", body)

    def test_fresh_csv_reports_running(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(monitor, "LOGS", Path(temp)), \
             patch.object(monitor, "active_service", return_value=True), \
             patch.object(monitor, "kiln_alive", return_value=True):
            Path(temp, "kiln_test.csv").write_text(
                "timestamp_utc,temperature_c,humidity_percent,reading_age_seconds,"
                "heater_on,humidifier_on,fan_on,sensor_fault,cpu_temperature_c\n"
                "2026-11-14T11:59:55.000Z,42,74,2,1,0,1,0,60\n"
            )
            title, body = monitor.report(dt.datetime(2026,11,14,12,tzinfo=dt.timezone.utc))
            self.assertIn("RUNNING", title)
            self.assertIn("42.00 °C", body)
            self.assertIn("**ON**", body)

    def test_stale_reading_never_reported_as_running(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(monitor, "LOGS", Path(temp)), \
             patch.object(monitor, "active_service", return_value=True), \
             patch.object(monitor, "kiln_alive", return_value=True):
            Path(temp, "kiln_test.csv").write_text(
                "timestamp_utc,temperature_c,humidity_percent,reading_age_seconds,"
                "heater_on,humidifier_on,fan_on,sensor_fault,cpu_temperature_c\n"
                "2026-11-14T11:39:00.000Z,42,74,2,1,0,1,0,60\n"
            )
            title, body = monitor.report(dt.datetime(2026,11,14,12,tzinfo=dt.timezone.utc))
            self.assertIn("CHECK KILN", title)
            self.assertIn("UNKNOWN (no fresh telemetry)", body)


if __name__ == "__main__":
    unittest.main()

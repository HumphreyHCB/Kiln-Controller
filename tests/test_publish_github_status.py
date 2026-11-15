import datetime as dt
import importlib.util
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
MODULE = SCRIPT_DIR / "publish_github_status.py"
spec = importlib.util.spec_from_file_location("kiln_status", MODULE)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def test_missing_csv_reports_problem(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(monitor, "LOGS", Path(temp)), \
             patch.object(monitor, "ARM_FILE", Path(temp) / "arm"), \
             patch.object(monitor, "active_service", return_value=True), \
             patch.object(monitor, "kiln_alive", return_value=True):
            (Path(temp) / "arm").touch()
            title, body = monitor.report(dt.datetime(2026,11,14,12,tzinfo=dt.timezone.utc))
            self.assertIn("CHECK KILN", title)
            self.assertIn("CSV measurement missing/stale", body)

    def test_fresh_csv_reports_running(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(monitor, "LOGS", Path(temp)), \
             patch.object(monitor, "ARM_FILE", Path(temp) / "arm"), \
             patch.object(monitor, "active_service", return_value=True), \
             patch.object(monitor, "kiln_alive", return_value=True):
            (Path(temp) / "arm").touch()
            Path(temp, "kiln_test.csv").write_text(
                "timestamp_utc,temperature_c,humidity_percent,reading_age_seconds,"
                "heater_on,humidifier_on,fan_on,sensor_fault,cpu_temperature_c\n"
                "2026-11-14T11:59:55.000Z,42,74,2,1,0,1,0,60\n"
            )
            title, body = monitor.report(dt.datetime(2026,11,14,12,tzinfo=dt.timezone.utc))
            self.assertIn("RUNNING", title)
            self.assertIn("42.00 °C", body)
            self.assertIn("**ON**", body)
            self.assertIn("Kiln process supervisor (Linux service) | Running", body)
            self.assertIn("Kiln C++ temperature/humidity controller | Running", body)
            self.assertIn("Independent safety controller (relay watchdog) | Running", body)
            self.assertIn("Not applicable — kiln is running", body)
            self.assertIn("Granted for this run — manual start authorised", body)
            self.assertNotIn("Relay-master OFF confirmation | NO", body)

    def test_stale_reading_never_reported_as_running(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(monitor, "LOGS", Path(temp)), \
             patch.object(monitor, "ARM_FILE", Path(temp) / "arm"), \
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


    def test_reboot_requires_manual_start_and_updates_issue(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(monitor, "LOGS", Path(temp)), \
             patch.object(monitor, "ARM_FILE", Path(temp) / "arm"), \
             patch.object(monitor, "active_service", return_value=False), \
             patch.object(monitor, "kiln_alive", return_value=False), \
             patch.object(monitor, "read_state", return_value={
                 "status": "running", "boot_id": "old-boot",
                 "started_utc": "2026-11-14T08:00:00Z"
             }), \
             patch.object(monitor, "boot_id", return_value="new-boot"), \
             patch.object(monitor, "last_csv_measurement",
                          return_value="2026-11-14T11:35:00Z"):
            title, body = monitor.report(dt.datetime(2026,11,14,12,tzinfo=dt.timezone.utc))
            self.assertIn("UNEXPECTED REBOOT", title)
            self.assertIn("RELAY STATE UNVERIFIED", body)
            self.assertIn("OFF not verified", body)
            self.assertNotIn("Relay-master OFF confirmation | NO", body)
            self.assertIn("2026-11-14T11:35:00Z", body)

    def test_explicit_off_acknowledged_after_reboot(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(monitor, "LOGS", Path(temp)), \
             patch.object(monitor, "ARM_FILE", Path(temp) / "arm"), \
             patch.object(monitor, "active_service",
                          side_effect=lambda name: name == "kiln-relay-master.service"), \
             patch.object(monitor, "kiln_alive", return_value=False), \
             patch.object(monitor, "confirm_off",
                          return_value=(True, "ALL_OFF acknowledged; 4 GPIO channels OFF")), \
             patch.object(monitor, "read_state", return_value={
                 "status": "running", "boot_id": "old-boot"
             }), \
             patch.object(monitor, "boot_id", return_value="new-boot"):
            title, body = monitor.report(dt.datetime(2026,11,14,12,tzinfo=dt.timezone.utc))
            self.assertIn("UNEXPECTED REBOOT", title)
            self.assertIn("ALL_OFF CONFIRMED", body)
            self.assertIn("OFF (confirmed by relay master)", body)
            self.assertIn("Confirmed OFF — all four relay channels acknowledged", body)
            self.assertIn("Not granted — manual start required", body)

    def test_orderly_reboot_still_requires_manual_start(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(monitor, "LOGS", Path(temp)), \
             patch.object(monitor, "ARM_FILE", Path(temp) / "arm"), \
             patch.object(monitor, "active_service", return_value=False), \
             patch.object(monitor, "kiln_alive", return_value=False), \
             patch.object(monitor, "read_state", return_value={
                 "status": "stopped", "boot_id": "old-boot"
             }), \
             patch.object(monitor, "boot_id", return_value="new-boot"):
            title, body = monitor.report(dt.datetime(2026,11,14,12,tzinfo=dt.timezone.utc))
            self.assertIn("MANUAL START REQUIRED", title)
            self.assertNotIn("UNEXPECTED REBOOT", title)


if __name__ == "__main__":
    unittest.main()

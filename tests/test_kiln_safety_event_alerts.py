"""Persistent controller incident forwarding and external heartbeat checks."""
import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import kiln_control_events as events
import kiln_issue_notifications as notify
import check_github_status_staleness as remote

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 17, 14, 0, tzinfo=UTC)


class PersistedEventTests(unittest.TestCase):
    def test_jsonl_reader_skips_partial_records_and_unrecognized_events(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_text(
                '{"id":"a","kind":"sensor_stale",'
                '"detected_utc":"2026-11-17T12:00:00Z","detail":"lost sensor"}\n'
                '{"id":"b","kind":"heater_timeout",'
                '"detected_utc":"2026-11-17T13:00:00Z","detail":"heater OFF"}\n'
                '{"id":"c","kind":"other",'
                '"detected_utc":"2026-11-17T13:00:00Z","detail":"ignored"}\n'
                '{"id":"broken",\n'
            )
            with patch.object(events, "EVENTS_FILE", path):
                incidents = events.recent_events()
                self.assertEqual([x["id"] for x in incidents], ["a", "b"])
                self.assertIn("heater OFF", events.issue_history())

    def test_each_persisted_control_incident_queues_one_notification(self):
        lifecycle = {"status": "running", "boot_id": "boot1",
                     "started_utc": "2026-11-17T12:00:00Z", "events": []}
        control_events = []
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(notify, "STATE_FILE", Path(temp, "notifications.json")), \
             patch.object(notify, "read_state", return_value=lifecycle), \
             patch.object(notify, "boot_id", return_value="boot1"), \
             patch.object(notify, "recent_events", side_effect=lambda limit=120: control_events), \
             patch.object(notify, "publish_notifications"):
            notify.notify("Kiln status — RUNNING", "healthy", "fake", now=NOW)
            control_events.append({
                "id": "stale1", "kind": "sensor_stale",
                "detected_utc": "2026-11-17T12:15:00Z",
                "detail": "reading stale for 20 seconds",
            })
            notify.notify("Kiln status — RUNNING", "healthy", "fake", now=NOW)
            notify.notify("Kiln status — RUNNING", "healthy", "fake", now=NOW)
            self.assertEqual(len(notify.load_state()["pending"]), 1)

            control_events.append({
                "id": "heater1", "kind": "heater_timeout",
                "detected_utc": "2026-11-17T13:15:00Z",
                "detail": "Heater turned off at 45.3 C",
            })
            notify.notify("Kiln status — RUNNING", "healthy", "fake", now=NOW)
            pending = notify.load_state()["pending"]
            self.assertEqual(len(pending), 2)
            self.assertIn("Bluetooth", pending[0]["body"])
            self.assertIn("watchdog", pending[1]["body"])

    def test_status_heartbeat_exactly_45_minutes_is_still_fresh(self):
        stamp = "<!-- KILN_STATUS_REPORT_UTC: 2026-11-17T13:15:00Z -->"
        self.assertIsNone(remote.alert_needed(stamp, NOW, []))
        self.assertIsNone(remote.alert_needed(
            "<!-- KILN_STATUS_REPORT_UTC: 2026-11-17T13:16:00Z -->",
            NOW, []))

    def test_stale_heartbeat_alerts_once_per_missing_heartbeat(self):
        stamp = "<!-- KILN_STATUS_REPORT_UTC: 2026-11-17T13:14:00Z -->"
        message = remote.alert_needed(stamp, NOW, [])
        self.assertIn("@HumphreyHCB", message)
        self.assertIn("46 minutes", message)
        self.assertIsNone(remote.alert_needed(stamp, NOW, [message]))
        new_stamp = "<!-- KILN_STATUS_REPORT_UTC: 2026-11-17T13:00:00Z -->"
        self.assertIsNotNone(remote.alert_needed(new_stamp, NOW, [message]))

    def test_missing_marker_and_future_timestamp(self):
        self.assertIn("unknown", remote.alert_needed("# no report", NOW, []))
        future = "<!-- KILN_STATUS_REPORT_UTC: 2026-11-17T15:00:00Z -->"
        self.assertIn("invalid future timestamp",
                      remote.alert_needed(future, NOW, []))


if __name__ == "__main__":
    unittest.main()

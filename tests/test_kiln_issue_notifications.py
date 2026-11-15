"""Notifications are event-only, deduplicated, retried after failed API calls."""
import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import kiln_issue_notifications as alerts

NOW = dt.datetime(2026, 11, 15, 15, 0, tzinfo=dt.timezone.utc)
CHECK = "Kiln status — CHECK KILN"
RUNNING = "Kiln status — RUNNING"
STOPPED = "Kiln status — OFF VERIFIED · MANUAL START REQUIRED"
UNCLEAN = "Kiln status — UNEXPECTED REBOOT · MANUAL RESTART REQUIRED"


class NotificationTests(unittest.TestCase):
    def test_first_observation_does_not_reannounce_existing_running_kiln(self):
        lifecycle = {"status": "running", "boot_id": "boot1",
                     "started_utc": "2026-11-15T14:00:00Z", "events": []}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(alerts, "STATE_FILE", Path(temp, "alert_state.json")), \
             patch.object(alerts, "read_state", return_value=lifecycle), \
             patch.object(alerts, "boot_id", return_value="boot1"), \
             patch.object(alerts, "publish_notifications"):
            alerts.notify(RUNNING, "healthy", "fake", now=NOW)
            self.assertEqual(alerts.load_state()["pending"], [])
            alerts.notify(RUNNING, "healthy", "fake", now=NOW)
            self.assertEqual(alerts.load_state()["pending"], [])

    def test_start_notification_once_per_new_service_run(self):
        lifecycle = {"status": "running", "boot_id": "boot1",
                     "started_utc": "2026-11-15T14:00:00Z", "events": []}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(alerts, "STATE_FILE", Path(temp, "alert_state.json")), \
             patch.object(alerts, "read_state", side_effect=lambda: lifecycle), \
             patch.object(alerts, "boot_id", return_value="boot1"), \
             patch.object(alerts, "publish_notifications"):
            alerts.notify(RUNNING, "healthy", "fake", now=NOW)
            lifecycle["started_utc"] = "2026-11-15T14:59:00Z"
            alerts.notify(RUNNING, "healthy", "fake", now=NOW)
            alerts.notify(RUNNING, "healthy", "fake", now=NOW)
            state = alerts.load_state()
            self.assertEqual(len(state["pending"]), 1)
            self.assertIn("successfully started", state["pending"][0]["body"])

    def test_fault_only_once_until_resolved(self):
        lifecycle = {"status": "running", "boot_id": "boot1",
                     "started_utc": "2026-11-15T14:00:00Z", "events": []}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(alerts, "STATE_FILE", Path(temp, "alert_state.json")), \
             patch.object(alerts, "read_state", return_value=lifecycle), \
             patch.object(alerts, "boot_id", return_value="boot1"), \
             patch.object(alerts, "publish_notifications"):
            for _ in range(3):
                alerts.notify(CHECK, "**Checks:** Bluetooth measurement stale",
                              "fake", now=NOW)
            self.assertEqual(len(alerts.load_state()["pending"]), 1)
            alerts.notify(RUNNING, "healthy", "fake", now=NOW)
            alerts.notify(CHECK, "**Checks:** Bluetooth measurement stale",
                          "fake", now=NOW)
            self.assertEqual(len(alerts.load_state()["pending"]), 2)

    def test_unclean_reboot_once_even_after_manual_restart(self):
        lifecycle = {"status": "running", "boot_id": "old",
                     "started_utc": "2026-11-14T20:00:00Z", "events": []}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(alerts, "STATE_FILE", Path(temp, "alert_state.json")), \
             patch.object(alerts, "read_state", side_effect=lambda: lifecycle), \
             patch.object(alerts, "boot_id", return_value="new"), \
             patch.object(alerts, "last_csv_measurement", return_value="2026-11-15T02:00:00Z"), \
             patch.object(alerts, "publish_notifications"):
            alerts.notify(UNCLEAN, "reboot", "fake", now=NOW)
            alerts.notify(UNCLEAN, "reboot", "fake", now=NOW)
            self.assertEqual(len(alerts.load_state()["pending"]), 1)
            lifecycle.update({
                "status": "running", "boot_id": "new",
                "started_utc": "2026-11-15T16:00:00Z",
                "events": [{
                    "kind": "suspected_unclean_reboot",
                    "detected_utc": "2026-11-15T16:00:00Z",
                    "previous_started_utc": "2026-11-14T20:00:00Z",
                }]
            })
            alerts.notify(RUNNING, "healthy", "fake", now=NOW)
            pending = alerts.load_state()["pending"]
            self.assertEqual(len(pending), 2)
            self.assertEqual(sum("unexpected" in p["body"].lower()
                                 or "ended without" in p["body"].lower()
                                 for p in pending), 1)

    def test_service_failure_caught_even_if_status_now_off(self):
        lifecycle = {"status": "running", "boot_id": "boot1",
                     "started_utc": "2026-11-15T14:00:00Z", "events": []}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(alerts, "STATE_FILE", Path(temp, "alert_state.json")), \
             patch.object(alerts, "read_state", side_effect=lambda: lifecycle), \
             patch.object(alerts, "boot_id", return_value="boot1"), \
             patch.object(alerts, "publish_notifications"):
            alerts.notify(RUNNING, "healthy", "fake", now=NOW)
            lifecycle.update({"status": "failed", "events": [{
                "kind": "controller_service_failure",
                "detected_utc": "2026-11-15T14:50:00Z",
                "detail": "systemd: exit-code; exit: exited 75"
            }]})
            alerts.notify(STOPPED, "off", "fake", now=NOW)
            alerts.notify(STOPPED, "off", "fake", now=NOW)
            pending = alerts.load_state()["pending"]
            self.assertEqual(len(pending), 1)
            self.assertIn("stopped unexpectedly", pending[0]["body"])

    def test_existing_github_comment_prevents_double_post(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(alerts, "STATE_FILE", Path(temp, "alert_state.json")):
            state = {"sent": [], "pending": [{"id": "abc123", "body": "Alert"}]}
            requests = []

            def fake_request(token, method, suffix, data=None):
                requests.append(method)
                if method == "GET":
                    return [{"body": "Alert\n\n<!-- KILN_EVENT_NOTIFY:abc123 -->"}]
                raise AssertionError("must not create a second comment")

            with patch.object(alerts, "github_request", side_effect=fake_request):
                alerts.publish_notifications(state, "fake")
            self.assertEqual(requests, ["GET"])
            self.assertEqual(alerts.load_state()["pending"], [])
            self.assertIn("abc123", alerts.load_state()["sent"])

    def test_new_event_posted_once_with_machine_marker(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(alerts, "STATE_FILE", Path(temp, "alert_state.json")):
            state = {"sent": [], "pending": [{"id": "abc", "body": "✅ Kiln started"}]}
            requests = []

            def fake_request(token, method, suffix, data=None):
                requests.append((method, data))
                return [] if method == "GET" else {"id": 42}

            with patch.object(alerts, "github_request", side_effect=fake_request):
                alerts.publish_notifications(state, "fake")
            self.assertEqual([x[0] for x in requests], ["GET", "POST"])
            self.assertIn("<!-- KILN_EVENT_NOTIFY:abc -->", requests[-1][1]["body"])
            self.assertEqual(alerts.load_state()["pending"], [])


if __name__ == "__main__":
    unittest.main()

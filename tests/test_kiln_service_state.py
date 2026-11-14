"""Lifecycle regression tests for clean shutdown and unexpected reboot."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import kiln_service_state as lifecycle


class LifecycleTests(unittest.TestCase):
    def test_clean_stop_and_new_boot_no_false_alarm(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(lifecycle, "STATE_FILE", Path(temp, "life.json")), \
             patch.object(lifecycle, "boot_id", side_effect=["boot-1", "boot-1", "boot-2"]), \
             patch.object(lifecycle, "last_csv_measurement", return_value="2026-11-14T12:00:00Z"):
            lifecycle.mark_start()
            with patch.dict(os.environ, {"SERVICE_RESULT": "success",
                                         "EXIT_CODE": "exited", "EXIT_STATUS": "0"}):
                lifecycle.mark_stop()
            lifecycle.mark_start()
            state = lifecycle.read_state()
            self.assertEqual(state["status"], "running")
            self.assertFalse(state.get("events"))

    def test_old_running_state_survives_skipped_service_on_reboot(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(lifecycle, "STATE_FILE", Path(temp, "life.json")), \
             patch.object(lifecycle, "boot_id", side_effect=["boot-1", "boot-2"]), \
             patch.object(lifecycle, "last_csv_measurement", return_value="2026-11-14T12:00:00Z"):
            lifecycle.mark_start()
            lifecycle.mark_stop()  # ExecStopPost fired by skipped service on new boot
            state = lifecycle.read_state()
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["boot_id"], "boot-1")

    def test_running_on_other_boot_records_interruption_on_manual_start(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(lifecycle, "STATE_FILE", Path(temp, "life.json")), \
             patch.object(lifecycle, "boot_id", side_effect=["boot-1", "boot-2"]), \
             patch.object(lifecycle, "last_csv_measurement", return_value="2026-11-14T12:00:00Z"):
            lifecycle.mark_start()
            lifecycle.mark_start()
            state = lifecycle.read_state()
            self.assertEqual(state["events"][-1]["kind"], "suspected_unclean_reboot")
            self.assertEqual(state["events"][-1]["last_measurement_before_restart_utc"], "2026-11-14T12:00:00Z")

    def test_service_failure_stored_and_keeps_manual_restart_policy(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(lifecycle, "STATE_FILE", Path(temp, "life.json")), \
             patch.object(lifecycle, "boot_id", return_value="boot-1"):
            lifecycle.mark_start()
            with patch.dict(os.environ, {"SERVICE_RESULT": "exit-code",
                                         "EXIT_CODE": "exited", "EXIT_STATUS": "2"}):
                lifecycle.mark_stop()
            state = lifecycle.read_state()
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["events"][-1]["kind"], "controller_service_failure")


if __name__ == "__main__":
    unittest.main()

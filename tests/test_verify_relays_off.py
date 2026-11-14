"""Tests for master acknowledgement; never touches real GPIO."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_relays_off as verifier


class FakeMaster:
    def __init__(self, *, accept_off=True, refuse_gpio=None):
        self.accept_off = accept_off
        self.refuse_gpio = refuse_gpio
        self.pending = b""
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, seconds):
        pass

    def connect(self, path):
        pass

    def sendall(self, data):
        self.commands.append(data)
        if data == b"ALL_OFF\n":
            self.pending = b"OK\n" if self.accept_off else b"ERR\n"
        elif data.startswith(b"GET "):
            self.pending = (b"1\n" if self.refuse_gpio is not None
                            and data == f"GET {self.refuse_gpio}\n".encode()
                            else b"0\n")
        else:
            self.pending = b"ERR\n"

    def recv(self, n):
        result, self.pending = self.pending[:n], self.pending[n:]
        return result


class RelayOffTests(unittest.TestCase):
    def test_no_contact_if_controller_may_be_running(self):
        with patch.object(verifier.socket, "socket", side_effect=AssertionError("must not connect")):
            ok, reason = verifier.confirm_off(lambda: False)
        self.assertFalse(ok)
        self.assertIn("Not checked", reason)

    def test_explicit_command_and_four_gets(self):
        master = FakeMaster()
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(verifier, "SUPERVISOR_LOCK", str(Path(temp, "supervisor.lock"))), \
             patch.object(verifier.socket, "socket", return_value=master):
            ok, reason = verifier.confirm_off(lambda: True)
        self.assertTrue(ok)
        self.assertIn("ALL_OFF acknowledged", reason)
        self.assertEqual(master.commands,
                         [b"ALL_OFF\n", b"GET 26\n", b"GET 19\n", b"GET 13\n", b"GET 6\n"])

    def test_no_confirmation_if_master_reports_any_on(self):
        master = FakeMaster(refuse_gpio=19)
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(verifier, "SUPERVISOR_LOCK", str(Path(temp, "supervisor.lock"))), \
             patch.object(verifier.socket, "socket", return_value=master):
            ok, reason = verifier.confirm_off(lambda: True)
        self.assertFalse(ok)
        self.assertIn("GPIO 19", reason)

    def test_no_confirmation_if_master_rejects_off(self):
        master = FakeMaster(accept_off=False)
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(verifier, "SUPERVISOR_LOCK", str(Path(temp, "supervisor.lock"))), \
             patch.object(verifier.socket, "socket", return_value=master):
            ok, reason = verifier.confirm_off(lambda: True)
        self.assertFalse(ok)
        self.assertIn("rejected ALL_OFF", reason)


if __name__ == "__main__":
    unittest.main()

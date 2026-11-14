"""Confirm the GPIO master's commanded OFF state when the kiln is stopped.

Only invoke while the controller is stopped. This module never commands ON.
The relay master is a single-client server: its normal kiln client must not
be interrupted. Take the same flock used by the supervisor before connecting.

Success means the relay master acknowledged gpiod OFF writes and its internal
state reports OFF. This is NOT physical contact or voltage measurement.
"""
import fcntl
from pathlib import Path
import socket

SOCKET_PATH = "/run/kiln-relay/relay.sock"
SUPERVISOR_LOCK = "/tmp/kiln-controller-supervisor.lock"
PINS = (26, 19, 13, 6)


def _response(sock):
    line = bytearray()
    while len(line) < 64:
        ch = sock.recv(1)
        if not ch:
            raise ConnectionError("Relay master disconnected")
        if ch == b"\n":
            return bytes(line)
        line.extend(ch)
    raise RuntimeError("Relay-master response too long")


def _send(sock, command):
    sock.sendall(command + b"\n")
    return _response(sock)


def confirm_off(safe_to_contact):
    """Return (confirmed: bool, detail: str).

    safe_to_contact is a callback that checks the controller service,
    kiln process, manual authorization marker, and relay-master service.
    The callback is evaluated both before and after acquiring the lock.
    """
    if not safe_to_contact():
        return False, "Not checked: controller may be active or relay master unavailable"
    try:
        with open(SUPERVISOR_LOCK, "a", encoding="ascii") as guard:
            try:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False, "Not checked: kiln supervisor owns the relay connection"
            if not safe_to_contact():
                return False, "Not checked: kiln state changed during verification"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(3.0)
                sock.connect(SOCKET_PATH)
                if _send(sock, b"ALL_OFF") != b"OK":
                    return False, "Relay master rejected ALL_OFF"
                for pin in PINS:
                    if _send(sock, f"GET {pin}".encode("ascii")) != b"0":
                        return False, f"Relay master did not report OFF on GPIO {pin}"
            return True, "ALL_OFF acknowledged; relay master's GPIO 26/19/13/6 states all report OFF"
    except (OSError, RuntimeError, ConnectionError) as exc:
        return False, f"Could not confirm relay-master OFF state: {exc}"

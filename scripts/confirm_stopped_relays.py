#!/usr/bin/env python3
"""Confirm all four GPIO-owner commanded relay states are OFF, stopped only.

Called after the stop helper has stopped the service and removed the arm
marker. This is not independent physical contact or voltage measurement.
"""
import sys

from publish_github_status import active_service, kiln_alive, ARM_FILE
from verify_relays_off import confirm_off


def safe_to_verify():
    return (not active_service("kiln-controller.service")
            and not kiln_alive()
            and not ARM_FILE.is_file()
            and active_service("kiln-relay-master.service"))


if __name__ == "__main__":
    ok, detail = confirm_off(safe_to_verify)
    print("[KILN STOP] " + detail, flush=True)
    sys.exit(0 if ok else 1)

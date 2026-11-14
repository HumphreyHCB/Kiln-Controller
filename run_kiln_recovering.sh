#!/usr/bin/env bash
# Run from the Kiln-Controller repository root, or call this script by path.
# The Pi's separate kiln-relay-master.service MUST be active.
# If Bluetooth readings stop for 60 sec: all OFF, wait 60 sec, restart kiln.
# Three RECOVERY retries after the initial run; then abort with error.
# A blocked C++/BlueZ call is killed by this supervisor as a second line of defence.
set -Eeuo pipefail
cd "$(dirname "$(realpath "$0")")"

mkdir -p logs
if ! command -v flock >/dev/null; then
    echo '[FATAL] flock utility is required.' >&2
    exit 2
fi
exec 9> /tmp/kiln-controller-supervisor.lock
if ! flock -n 9; then
    echo '[FATAL] Another kiln recovery supervisor is already running.' >&2
    exit 2
fi
if ps -C kiln -o stat= | grep -Eq '^[[:space:]]*[^Z[:space:]]' >/dev/null; then
    echo '[FATAL] An existing kiln process is running. Stop it before starting another.' >&2
    exit 2
fi
if ! systemctl is-active --quiet kiln-relay-master.service; then
    echo '[FATAL] kiln-relay-master.service is not active. Cannot run safely.' >&2
    exit 2
fi

cmake --build build -j2

scratch="$(mktemp -d /tmp/kiln-supervisor.XXXXXXXX)"
health="$scratch/sensor-fresh"
child_pid=''

stop_child() {
    if [[ -z "$child_pid" ]]; then return; fi
    if kill -0 "$child_pid" 2>/dev/null; then
        kill -TERM "$child_pid" 2>/dev/null || true
        for ((i=0; i<10; i++)); do
            if ! kill -0 "$child_pid" 2>/dev/null; then break; fi
            sleep 0.5
        done
        if kill -0 "$child_pid" 2>/dev/null; then
            echo '[SUPERVISOR] Child unresponsive; sending SIGKILL.' >&2
            kill -KILL "$child_pid" 2>/dev/null || true
        fi
    fi
    wait "$child_pid" 2>/dev/null || true
    child_pid=''
}

force_off() {
    python3 "$(dirname "$(realpath "$0")")/force_relays_off.py"
}

on_signal() {
    trap - INT TERM HUP
    echo '[SUPERVISOR] Stop signal received. Stopping kiln.' >&2
    stop_child
    force_off || true
    exit 130
}
trap 'rm -rf "$scratch"' EXIT
trap on_signal INT TERM HUP

max_retries=3
for ((retry=0; retry<=max_retries; retry++)); do
    if (( retry == 0 )); then
        echo '[SUPERVISOR] Starting kiln (initial run).'
    else
        echo "[RECOVERY] Restart attempt $retry/$max_retries."
    fi
    rm -f "$health"
    started_at="$(date +%s)"
    KILN_SENSOR_HEARTBEAT_FILE="$health" ./build/kiln &
    child_pid=$!
    sensor_timed_out=0

    while kill -0 "$child_pid" 2>/dev/null; do
        sleep 2
        now="$(date +%s)"
        last_sensor="$started_at"
        if [[ -f "$health" ]]; then
            last_sensor="$(stat -c %Y "$health")"
        fi
        if (( now - last_sensor >= 60 )); then
            echo '[RECOVERY] No fresh Bluetooth data for >=60s. Terminating kiln process.' >&2
            sensor_timed_out=1
            stop_child
            break
        fi
    done

    status=0
    if [[ -n "$child_pid" ]]; then
        wait "$child_pid" || status=$?
        child_pid=''
    fi
    if (( sensor_timed_out )); then status=75; fi

    # This confirms that the relay master is reachable and accepts an OFF
    # command AFTER the old client's connection has closed.
    if ! force_off; then
        echo '[FATAL] Relay master did not confirm OFF. No automatic restart.' >&2
        exit 2
    fi

    if (( status == 0 )); then
        echo '[SUPERVISOR] Kiln stopped normally.'
        exit 0
    fi
    if (( status != 75 )); then
        echo "[FATAL] Kiln exited with status $status, NOT a sensor recovery fault. No retries." >&2
        exit "$status"
    fi
    if (( retry == max_retries )); then
        echo '[FATAL] Three Bluetooth recovery retries exhausted. Relays OFF; ending supervisor.' >&2
        exit 1
    fi
    echo "[RECOVERY] Sensor fault. All relays OFF. Resting 60 seconds before retry $((retry+1))/$max_retries."
    sleep 60
done

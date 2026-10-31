#!/usr/bin/env bash
# Install the kiln relay master service and C++ Unix-socket client.
# Run this AS YOUR NORMAL PI USER, from the Kiln-Controller repo directory.
# CAUTION: perform the installation only with all loads disconnected.
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
    echo 'ERROR: Run this script as your normal Pi user, not sudo.' >&2
    exit 1
fi
if [[ ! -f src/RelayController.cpp || ! -f src/RelayController.hpp ]]; then
    echo 'ERROR: Run from ~/repos/Kiln-Controller, where src/RelayController.cpp exists.' >&2
    exit 1
fi

PI_USER=$(id -un)
BACKUP="src/relay-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
cp -p src/RelayController.cpp src/RelayController.hpp "$BACKUP/"
echo "Backup of original C++ files: $BACKUP"

# Stop the old, flawed watchdog. Stop any old/suspended kiln process
# so it cannot keep its exclusive GPIO line requests.
sudo systemctl disable --now kiln-relay-watchdog.service 2>/dev/null || true
sudo systemctl stop kiln-relay-master.service 2>/dev/null || true
sudo pkill -KILL -x kiln 2>/dev/null || true

sudo apt-get update
sudo apt-get install -y python3-libgpiod
sudo install -d -m 0755 /usr/local/lib/kiln
sudo tee /usr/local/lib/kiln/relay_master.py >/dev/null <<'KILN_RELAY_PY'
#!/usr/bin/env python3
"""Single GPIO owner for the kiln, with a 10-second client heartbeat timeout.

All four Seengreat CH1-CH4 outputs are active-low and held OFF by default.
The kiln C++ process talks over a local Unix socket, never to GPIO directly.
"""
import os
import pwd
import signal
import socket
import sys
import time

import gpiod
from gpiod.line import Direction, Value

PINS = (26, 19, 13, 6)
SOCKET_PATH = '/run/kiln-relay/relay.sock'
TIMEOUT_SECONDS = 10.0
running = True


def request_stop(signum, frame):
    global running
    running = False


def log(message):
    print(f'[kiln-relay-master] {message}', flush=True)


def main(socket_owner):
    owner = pwd.getpwnam(socket_owner)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    # This is the ONLY process allowed to request these GPIO lines.
    # Logical INACTIVE = physical HIGH = Seengreat relay OFF.
    settings = gpiod.LineSettings(
        direction=Direction.OUTPUT,
        active_low=True,
        output_value=Value.INACTIVE,
    )
    with gpiod.request_lines(
        '/dev/gpiochip0',
        consumer='kiln-relay-master',
        config={PINS: settings},
    ) as request:
        states = {pin: False for pin in PINS}

        def all_off():
            request.set_values({pin: Value.INACTIVE for pin in PINS})
            states.update({pin: False for pin in PINS})

        all_off()
        os.makedirs(os.path.dirname(SOCKET_PATH), mode=0o755, exist_ok=True)
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(SOCKET_PATH)
            os.chown(SOCKET_PATH, owner.pw_uid, owner.pw_gid)
            os.chmod(SOCKET_PATH, 0o600)
            server.listen(1)
            server.settimeout(1.0)
            log('GPIO CH1-CH4 owned, all OFF; waiting for kiln client')
            try:
                while running:
                    try:
                        client, _ = server.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        if not running:
                            break
                        raise
                    # Always start a session with all outputs de-energised.
                    all_off()
                    log('Client connected')
                    with client:
                        client.settimeout(0.5)
                        buffer = b''
                        last_command = time.monotonic()
                        while running:
                            if time.monotonic() - last_command >= TIMEOUT_SECONDS:
                                log('HEARTBEAT TIMEOUT: forcing all relays OFF')
                                break
                            try:
                                incoming = client.recv(256)
                            except socket.timeout:
                                continue
                            except (ConnectionError, OSError):
                                break
                            if not incoming:
                                break
                            buffer += incoming
                            if len(buffer) > 1024:
                                log('Oversized command: dropping client')
                                break
                            while b'\n' in buffer:
                                raw, buffer = buffer.split(b'\n', 1)
                                tokens = raw.decode('ascii', errors='replace').strip().split()
                                response = 'ERR bad command'
                                if tokens == ['PING']:
                                    response = 'OK'
                                elif tokens == ['ALL_OFF']:
                                    all_off()
                                    response = 'OK'
                                elif len(tokens) == 2 and tokens[0] in ('ON', 'OFF', 'GET'):
                                    try:
                                        pin = int(tokens[1])
                                    except ValueError:
                                        pin = -1
                                    if pin in PINS:
                                        if tokens[0] == 'GET':
                                            response = '1' if states[pin] else '0'
                                        else:
                                            on = tokens[0] == 'ON'
                                            # Set physical pin first. Only record state on success.
                                            request.set_value(pin, Value.ACTIVE if on else Value.INACTIVE)
                                            states[pin] = on
                                            response = 'OK'
                                if response.startswith('ERR'):
                                    log(f'Rejected command: {raw!r}')
                                else:
                                    # GET in IsOn() is also our heartbeat.
                                    last_command = time.monotonic()
                                try:
                                    client.sendall((response + '\n').encode('ascii'))
                                except OSError:
                                    break
                    # Ctrl+C, SIGKILL, Ctrl+Z timeout, socket failure: OFF.
                    all_off()
                    log('Client disconnected or timed out; all relays OFF')
            finally:
                all_off()
                try:
                    os.unlink(SOCKET_PATH)
                except FileNotFoundError:
                    pass
                log('Shutting down with relays OFF')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: relay_master.py <kiln-username>')
    try:
        main(sys.argv[1])
    except Exception as exc:
        log(f'FATAL: {exc}')
        raise
KILN_RELAY_PY
sudo chmod 0755 /usr/local/lib/kiln/relay_master.py
sudo tee /etc/systemd/system/kiln-relay-master.service >/dev/null <<'KILN_RELAY_SERVICE'
[Unit]
Description=Kiln relay master (owns GPIO, 10-second watchdog)
After=local-fs.target

[Service]
Type=simple
User=root
RuntimeDirectory=kiln-relay
RuntimeDirectoryMode=0755
ExecStart=/usr/bin/python3 -u /usr/local/lib/kiln/relay_master.py KILN_USER_PLACEHOLDER
Restart=always
RestartSec=1
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
KILN_RELAY_SERVICE
sudo sed -i "s/KILN_USER_PLACEHOLDER/$PI_USER/g" /etc/systemd/system/kiln-relay-master.service
sudo chmod 0644 /etc/systemd/system/kiln-relay-master.service

echo 'Starting the persistent GPIO master service...'
sudo systemctl daemon-reload
sudo systemctl enable --now kiln-relay-master.service
sleep 1
if ! sudo systemctl is-active --quiet kiln-relay-master.service; then
    echo 'ERROR: Relay master did not start. Check for old gpioset processes holding lines.' >&2
    sudo journalctl -u kiln-relay-master.service -n 35 --no-pager
    echo 'Check: ps -eo pid,stat,args | grep -E "gpioset|kiln"' >&2
    echo 'Your original C++ files have NOT been changed.' >&2
    exit 1
fi
if ! [[ -S /run/kiln-relay/relay.sock ]]; then
    echo 'ERROR: Relay master socket is missing, even though service is active.' >&2
    sudo journalctl -u kiln-relay-master.service -n 35 --no-pager
    exit 1
fi

# Replace only the relay-specific C++ files. All other project code stays as-is.
cat > src/RelayController.hpp <<'KILN_HPP'
#pragma once

#include <string>

// Same application-facing interface as before. GPIO is now owned by
// an independent systemd service, not by this C++ process.
class RelayController {
public:
    class Relay {
    public:
        void On();
        void Off();
        bool IsOn() const;

    private:
        friend class RelayController;
        explicit Relay(RelayController& owner);
        RelayController& owner_;
        int gpio_ = -1;
    };

    Relay Heater;
    Relay Humidifier;
    Relay Fan;

    explicit RelayController(
        const std::string& configPath = "config/relays.json");
    ~RelayController() noexcept;

    void AllOff();

    RelayController(const RelayController&) = delete;
    RelayController& operator=(const RelayController&) = delete;

private:
    int socket_ = -1;
    void set(Relay& relay, bool on);
    bool get(const Relay& relay) const;
    std::string transact(const std::string& command) const;
};
KILN_HPP
cat > src/RelayController.cpp <<'KILN_CPP'
#include "RelayController.hpp"

#include <cerrno>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <nlohmann/json.hpp>

namespace {
constexpr const char* SOCKET_PATH = "/run/kiln-relay/relay.sock";

int relayToGpio(int relayNumber) {
    switch (relayNumber) {
        case 1: return 26;
        case 2: return 19;
        case 3: return 13;
        case 4: return 6;
        default: throw std::invalid_argument("Invalid relay number");
    }
}
} // namespace

// Each Relay object knows which controller owns its socket connection.
RelayController::Relay::Relay(RelayController& owner) : owner_(owner) {}

void RelayController::Relay::On()  { owner_.set(*this, true); }
void RelayController::Relay::Off() { owner_.set(*this, false); }

// This asks the master service for the ACTUAL commanded state and
// refreshes its 10-second heartbeat. A frozen kiln sends no requests.
bool RelayController::Relay::IsOn() const { return owner_.get(*this); }

RelayController::RelayController(const std::string& configPath)
    : Heater(*this), Humidifier(*this), Fan(*this)
{
    std::ifstream file(configPath);
    if (!file) throw std::runtime_error("Cannot open relay config: " + configPath);
    const nlohmann::json config = nlohmann::json::parse(file);
    const int heater = relayToGpio(config.at("heaterRelay").get<int>());
    const int humidifier = relayToGpio(config.at("humidifierRelay").get<int>());
    const int fan = relayToGpio(config.at("fanRelay").get<int>());
    if (heater == humidifier || heater == fan || humidifier == fan)
        throw std::invalid_argument("Two devices assigned to the same relay");

    Heater.gpio_ = heater;
    Humidifier.gpio_ = humidifier;
    Fan.gpio_ = fan;

    // Establish a persistent Unix socket connection to the GPIO owner.
    // This socket closes automatically on process crash/SIGKILL.
    int fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) throw std::runtime_error("Cannot create relay socket");
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    if (std::strlen(SOCKET_PATH) >= sizeof(address.sun_path)) {
        ::close(fd);
        throw std::runtime_error("Relay socket path too long");
    }
    std::strcpy(address.sun_path, SOCKET_PATH);
    if (::connect(fd, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) < 0) {
        const std::string error = std::strerror(errno);
        ::close(fd);
        throw std::runtime_error("Cannot connect to relay master: " + error);
    }
    socket_ = fd;

    // Bound requests so a missing/unresponsive service doesn't hang the kiln.
    timeval timeout{};
    timeout.tv_sec = 3;
    ::setsockopt(socket_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    ::setsockopt(socket_, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    try {
        if (transact("PING") != "OK")
            throw std::runtime_error("Relay master rejected PING");
    } catch (...) {
        ::close(socket_);
        socket_ = -1;
        throw;
    }
}

// Request/response protocol: e.g. "ON 26\n" -> "OK\n".
// The master service retains exclusive ownership of the GPIO pins.
std::string RelayController::transact(const std::string& command) const {
    if (socket_ < 0) throw std::runtime_error("Relay master not connected");
    const std::string message = command + "\n";
    size_t sent = 0;
    while (sent < message.size()) {
        ssize_t n = ::send(socket_, message.data() + sent, message.size() - sent, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) throw std::runtime_error("Relay master write failed");
        sent += static_cast<size_t>(n);
    }
    std::string reply;
    while (reply.size() < 256) {
        char ch = 0;
        ssize_t n = ::recv(socket_, &ch, 1, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) throw std::runtime_error("Relay master disconnected or timed out");
        if (ch == '\n') break;
        reply.push_back(ch);
    }
    if (reply.rfind("ERR", 0) == 0)
        throw std::runtime_error("Relay master: " + reply);
    return reply;
}

void RelayController::set(Relay& relay, bool on) {
    const auto result = transact(std::string(on ? "ON " : "OFF ") + std::to_string(relay.gpio_));
    if (result != "OK") throw std::runtime_error("Unexpected relay master reply: " + result);
}

bool RelayController::get(const Relay& relay) const {
    const auto result = transact("GET " + std::to_string(relay.gpio_));
    if (result == "1") return true;
    if (result == "0") return false;
    throw std::runtime_error("Unexpected relay master reply: " + result);
}

void RelayController::AllOff() {
    if (transact("ALL_OFF") != "OK")
        throw std::runtime_error("Relay master refused ALL_OFF");
}

RelayController::~RelayController() noexcept {
    if (socket_ >= 0) {
        // No contact with a live master? Closing the connection causes
        // the master to force all outputs OFF itself.
        try { AllOff(); } catch (...) {}
        ::shutdown(socket_, SHUT_RDWR);
        ::close(socket_);
        socket_ = -1;
    }
}
KILN_CPP

echo 'Rebuilding Kiln-Controller...'
cmake -S . -B build
cmake --build build -j 1

echo
echo 'INSTALLED: kiln-relay-master.service is enabled on boot.'
echo 'Its GPIO outputs are continuously held OFF until the C++ kiln client issues commands.'
echo 'The service switches ALL outputs OFF on client disconnect or after 10 seconds with no commands.'
echo 'Check: sudo systemctl status kiln-relay-master.service'
echo 'Logs:  sudo journalctl -u kiln-relay-master.service -f'
echo 'Pins:  gpioinfo gpiochip0 | grep -E "(line.*( 6:| 13:| 19:| 26:)|kiln-relay-master)"'
echo 'Then run your normal: bash buildandRun.sh'
echo 'WARNING: Test with NO loads attached. Still requires independent hardware thermal cutoff.'

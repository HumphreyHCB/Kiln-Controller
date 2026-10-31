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

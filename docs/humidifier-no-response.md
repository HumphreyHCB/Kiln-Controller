# Humidifier no-response protection

The humidifier uses water from a small tank inside the kiln. If the tank is
empty, the controller must not leave the humidifier relay running indefinitely.
The no-response watchdog monitors fresh humidity readings while the humidifier
is commanded ON and the kiln temperature is in the configured humidification
range.

## Default behaviour

The defaults in `config/kiln-control.json` require humidity to rise by at least
**1.0 percentage point within 15 continuous minutes of humidifier ON time**.
The watchdog tracks the highest fresh reading in that window, so a brief sensor
dip does not erase observed progress. A stale reading or temperature outside
the allowed range pauses the current observation window and does not count as a
failed attempt.

If the humidity does not rise enough, the controller turns OFF only the
humidifier and waits **5 minutes** before trying again, provided normal humidity
control still requests humidification. A humidity increase of at least the
configured amount during an attempt resets the consecutive-failure count. The
normal humidity cutoff also clears that count.

After **3 consecutive failed windows**, the controller locks the humidifier OFF
for the remainder of that controller run and records a durable event. The
existing GitHub monitor posts the event to Issue #1; its notification workflow
then posts an `@HumphreyHCB` mention. This alert path runs on the existing status
polling interval, so it can take up to about 20 minutes to appear. The kiln is
not shut down: temperature regulation and heater control continue normally.

Check/refill the tank and inspect the humidifier before manually restarting
the controller. The lockout is in memory and is cleared by a controller
restart, which is an explicit operator action in this installation.

## Configuration

These optional keys live under `humidity` in `config/kiln-control.json`:

| Key | Default | Meaning |
|---|---:|---|
| `noIncreaseTimeoutMinutes` | 15 | Continuous humidifier ON time allowed without enough RH progress |
| `minimumRisePercentagePoints` | 1.0 | Minimum rise in the highest fresh RH reading within a window |
| `retryDelayMinutes` | 5 | Humidifier OFF time after a failed window |
| `maxConsecutiveNoIncreaseAttempts` | 3 | Failed windows before humidifier-only lockout |

Old config files remain valid and receive these defaults. All durations,
thresholds, and the attempt limit are validated as positive values.

The watchdog does not prove that the tank is empty. A blocked wick, failed
humidifier, sensor placement, or unusually strong moisture absorption can also
produce little measured RH rise. It reports a humidification no-response
condition for the operator to inspect.

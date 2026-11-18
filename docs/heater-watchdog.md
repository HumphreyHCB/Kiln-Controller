# Heater continuous-ON watchdog and fan circulation settings

This is an **additional software timeout** for a heater which is working but
cannot quite reach the usual OFF threshold. It is **not** an independent
overtemperature cutout, and it does not change relay-master behavior.

## Settings in config/kiln-control.json

The PR adds these fields while leaving the repository's existing setpoints
unchanged:

```json
{
  "temperature": {
    "min": 38.0,
    "max": 46.0,
    "stopBeforeMax": 2.5,
    "maxContinuousHeaterOnMinutes": 60,
    "maxConsecutiveHeaterTimeouts": 3
  },
  "humidity": {
    "min": 70.0,
    "max": 80.0,
    "stopBeforeMax": 5.0
  },
  "maxReadingAgeSeconds": 60,
  "fanCirculation": {
    "offBeforePulseMinutes": 20,
    "pulseMinutes": 5
  }
}
```


**If your Pi has custom temperature/humidity targets (e.g. 40.5–48°C and
70–78% RH), preserve those values!** The JSON above illustrates the
repository defaults; this PR does not intentionally change your thermostat
targets. Each duration must be a positive integer minute count, and
maxConsecutiveHeaterTimeouts must be a positive integer count.

For an older local JSON that does not contain the new fields, the controller
uses fallback values of 60 minutes, 3 consecutive heater timeouts, 20 minutes,
and 5 minutes respectively.

### Heater timeout: maxContinuousHeaterOnMinutes

- Starts timing only at the **first fresh sensor reading at or above the
  configured minimum while the heater is ON**. Time spent initially warming
  up below the minimum is **not counted**.
- Uses monotonic time. Once the timer starts, a brief dip below minimum does
  not reset it, but an actual heater-OFF transition resets the entire cycle.
- After the specified number of minutes **since first reaching minimum**,
  forces the heater OFF only if the current Bluetooth reading is fresh and
  temperature is within [temperature.min, temperature.max].
- If the heater is still trying to reach the configured minimum, this watchdog
  does **not** interrupt warm-up. The existing sensor timeout and relay-master
  protections remain in force.
- If the heater is already stopped by its usual threshold
  (max - stopBeforeMax), the normal thermostat handles that transition.
- After a watchdog OFF, the regular hysteresis logic leaves the heater OFF
  until temperature falls below temperature.min, at which point a new heating
  cycle may start. The continuous-ON timer resets at each actual OFF.
- **maxConsecutiveHeaterTimeouts: 3** — after three consecutive watchdog
  trips without an intervening normal thermostat OFF cycle, the controller
  commands **all four relays OFF**, records a
  **heater_timeout_shutdown** event, and exits with a fatal status. The
  supervisor confirms ALL_OFF and does not retry; the service remains stopped
  until an operator inspects the kiln and uses the manual start command. The
  count survives Bluetooth recovery restarts; a deliberate manual start clears
  it after the build succeeds. Change this positive integer in the temperature
  section of the JSON config.
- The watchdog writes a **[HEATER WATCHDOG]** line to the systemd journal
  **and** a persistent incident to logs/kiln_control_events.jsonl when it
  forces the heater OFF. GitHub Issue #1 shows the event history and creates
  a one-shot @mention notification on the next successful status publish.
  The repeated-timeout shutdown gets its own event and alert.

**Example for a 40.5–48°C range, stopBeforeMax 1.5°C, 60-minute timeout:**

- Normal heater ON below 40.5°C.
- Normal OFF if temperature reaches 46.5°C while heating.
- If heating lasts one hour and the current *fresh* temperature is 45.5°C,
  the watchdog turns the heater OFF anyway.
- It will only turn back ON after temperature falls below 40.5°C.
- If after one hour of initial warm-up the kiln is still at 39°C, the
  watchdog timer has **not even started**. It starts when the temperature
  first reaches at least 40.5°C; after another 60 minutes, it may force OFF
  if the reading is still fresh and within range.

This software watchdog cannot act if the C++ process/OS is hung; the separate
relay master's independent 10-second *communication* timeout helps on
stalled communications, but **neither is a substitute for an independent,
correctly rated physical overtemperature cutout and safe heater wiring**.

### CSV temperature settings

Every CSV data row now repeats the configured temperature minimum, maximum,
stop-before-max margin, the resulting heater OFF threshold
(maximum minus margin), the continuous heater ON limit, and the consecutive
timeout limit. This keeps the setpoints beside the measurements when comparing
runs created with different configs.

### Fan circulation: offBeforePulseMinutes and pulseMinutes

The existing FanCirculationScheduler already turns the fan ON when the
heater/humidifier needs it. Separately, it sends an **extra circulation pulse**
after enough fan-OFF time has accumulated.

- **offBeforePulseMinutes: 20** — trigger an extra pulse after 20
  accumulated minutes with the fan OFF.
- **pulseMinutes: 5** — keep the extra circulation pulse active for 5 minutes,
  even if the heater and humidifier do not require it.
- Five uninterrupted minutes of fan ON also resets the accumulated OFF timer,
  as before.
- These are **not** post-heater shutdown fan-run-on settings. They configure
  the existing periodic circulation pulses, not the mandatory fan ON while
  heating/humidifying.
- Sensor faults continue to force all relays OFF, including the fan.

## Starting and stopping (no arguments)

The two operator scripts announce the operation immediately. They also
update Issue #1 when the operation completes and leave a human-readable
start/stop **comment** (not a notification alarm). Real heater watchdog
and sensor-stale events do generate @mention alerts.

**Stop** (relay-master OFF acknowledgement required):

```bash
cd ~/repos/Kiln-Controller
sudo bash scripts/stop_kiln.sh
```


**Start** (full clean rebuild, configure, compile as service user, manual arm,
start controller, re-enable GitHub timer, publish status):

```bash
cd ~/repos/Kiln-Controller
sudo bash scripts/start_kiln.sh
```


No arguments are needed. To perform a full clean rebuild and restart,
run the STOP command, then the START command. The scripts do not
reboot the Raspberry Pi or restart the independent relay master.

**Before you can use these scripts:** merge the PR and pull it to the Pi.
The new stop script is not on the Pi until the PR is deployed.

## Alerts and stale data

- The C++ controller already commands all relays OFF when fresh Bluetooth
  readings are unavailable for about 20 seconds. After 60 seconds without
  fresh readings, its supervisor may perform limited automatic sensor recovery.
- The update adds a persistent **sensor_stale** incident recorded at 20 seconds.
  Brief dropouts are kept even if the process recovers before the next GitHub
  20-minute report.
- A **heater_timeout** incident is also persisted after the relay-master
  acknowledges the forced heater OFF. Reaching the configured consecutive
  timeout limit records **heater_timeout_shutdown** after all relays are
  commanded OFF.
- Both incidents appear under **Recent controller safety events** in Issue #1,
  and cause event comments + a GitHub Actions bot @mention on the next
  successful Pi status publication. They do not create a comment every 5 seconds.
- Separately, GitHub Actions checks the Issue's embedded report timestamp
  every ~10 minutes. If it has not advanced for **45 minutes**, an external
  bot comments with an @mention once per missed-heartbeat incident. It does
  not rely on the Pi's timer being enabled or its internet working.
- Scheduled GitHub Actions and GitHub notifications are not real-time
  or safety-certified. A power failure is not guaranteed to cause a prompt
  alert, and only a physical thermal cutoff protects against all software
  failures.

## Deploying without losing your current setpoints

The repository defaults are not your live Pi settings. Before pulling, save
your tuned temperature and humidity ranges. If Git reports local changes in
config/kiln-control.json, stash that file before pulling. Then copy the
temperature/humidity thresholds from your saved settings into the new JSON,
retaining the new timeout and fanCirculation keys.

**Do not delete the config directory or the logs directory during rebuild.**
The new start script cleans and recreates only the CMake build directory.

See docs/github-status.md for existing service setup.

**Do not rely on this software watchdog as a physical safety cutoff.**
Verify actual heater shutdown safely, with the heater load isolated, before
trusting new control behavior. The Pi remains manual-start-only after a reboot.

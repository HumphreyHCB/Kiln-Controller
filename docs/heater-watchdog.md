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
    "maxContinuousHeaterOnMinutes": 60
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
targets. Each new timing must be a positive integer minute count.

For an older local JSON that does not contain the new fields, the controller
uses fallback values of 60 minutes, 20 minutes, and 5 minutes respectively.

### Heater timeout: maxContinuousHeaterOnMinutes

- Counts real **continuous commanded heater-ON time** using monotonic time.
- After the specified time has elapsed, it **forces the heater OFF only if
  the most recent Bluetooth reading is fresh and the kiln temperature is
  inside the configured [temperature.min, temperature.max] interval**.
- If the heater is still trying to reach the configured minimum, this watchdog
  does **not** interrupt warm-up. The existing sensor timeout and relay-master
  protections remain in force.
- If the heater is already stopped by its usual threshold
  (max - stopBeforeMax), the normal thermostat handles that transition.
- After a watchdog OFF, the regular hysteresis logic leaves the heater OFF
  until temperature falls below temperature.min, at which point a new heating
  cycle may start. The continuous-ON timer resets at each actual OFF.
- The watchdog writes a **[HEATER WATCHDOG]** line to the kiln controller's
  systemd journal each time it forces OFF. There is no automatic power cut or
  system service restart.

**Example for a 40.5–48°C range, stopBeforeMax 1.5°C, 60-minute timeout:**

- Normal heater ON below 40.5°C.
- Normal OFF if temperature reaches 46.5°C while heating.
- If heating lasts one hour and the current *fresh* temperature is 45.5°C,
  the watchdog turns the heater OFF anyway.
- It will only turn back ON after temperature falls below 40.5°C.
- If at one hour the kiln is still at 39°C, the watchdog does not force OFF
  until a fresh valid reading enters the 40.5–48°C interval.

This software watchdog cannot act if the C++ process/OS is hung; the separate
relay master's independent 10-second *communication* timeout helps on
stalled communications, but **neither is a substitute for an independent,
correctly rated physical overtemperature cutout and safe heater wiring**.

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

## Apply the change to an already-running Pi

**Configuration is read at C++ program startup, not hot-reloaded.** Stop the
kiln deliberately and check that it has stopped before applying changes.

If you previously edited your tracked config locally, a Git pull may refuse
to overwrite it. Save your settings first:

```bash
cd ~/repos/Kiln-Controller
cp config/kiln-control.json ~/kiln-control-backup-before-watchdog.json
sudo systemctl stop kiln-controller.service

# ONLY if your local config has uncommitted changes and blocks git pull:
git stash push -m "Preserve my kiln setpoints" -- config/kiln-control.json

git pull --ff-only
nano config/kiln-control.json

# Retain your current min/max/stopBeforeMax and humidity targets,
# while adding/changing the new timeout and fanCirculation keys.
sudo bash scripts/start_kiln.sh
sudo journalctl -u kiln-controller.service -n 45 --no-pager
```


The existing recovery supervisor automatically runs cmake --build at startup.
Do not restart the independent relay master or the GitHub timer just to
activate these configuration changes.

**Do not deploy or test heater control changes while leaving an unattended
heater running.** Verify the thermostat and watchdog OFF commands safely with
the heater load isolated before relying on them.

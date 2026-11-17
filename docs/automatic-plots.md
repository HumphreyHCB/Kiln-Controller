# Automatically commit kiln plots every 12 hours

This is **separate from the C++ controller and relay master**. It never
commands relays, changes temperature thresholds, restarts the kiln, or edits
CSV logs. The original CSV format already has all required columns.

Once installed, the Raspberry Pi publishes **8 fixed PNGs** into
[docs/plots](plots/README.md), covering *only* the last **24 hours** and
**7 days**. Each publication replaces those paths via **one GitHub commit**.
It is intentional that Git history grows by two image-update commits/day.

## Prerequisite: GitHub personal access token permission

The status monitor already uses a GitHub fine-grained token stored outside
this repository:

`/home/humphrey/.config/kiln/github-token`

Previously, this token only needed **Issues: Read and write**. The new plot
publisher additionally needs **Contents: Read and write** on the
`HumphreyHCB/Kiln-Controller` repository, because it creates
Git objects and commits. Keep **Issues: Read and write** enabled for the status
monitor and alert comments. Do not put the token into the repository or
command-line Git URLs.

Edit or replace the fine-grained PAT in your
[GitHub token settings](https://github.com/settings/personal-access-tokens).
If replacing the token, keep it in that same file with mode 600 and preserve
the Issues permission so your existing monitoring still works.

The publisher uses GitHub's **Git Data REST API**. It does **not** need
your local Git checkout to have write credentials or a configured SSH key.
It only commits these nine remote paths:

```text
docs/plots/temperature_24h.png
docs/plots/humidity_24h.png
docs/plots/pi_cpu_24h.png
docs/plots/relays_24h.png
docs/plots/temperature_7d.png
docs/plots/humidity_7d.png
docs/plots/pi_cpu_7d.png
docs/plots/relays_7d.png
docs/plots/last_updated.json
```


The last JSON file is a small UTC timestamp/coverage report. There are **no
long-term/all-time plots**.

## Install on the Raspberry Pi

After **merging the PR**:

```bash
cd ~/repos/Kiln-Controller
git pull --ff-only
bash scripts/install_kiln_plots.sh
```


The installer installs Debian/Raspberry Pi OS `python3-matplotlib`,
installs and enables the systemd timer, and makes **one immediate
publication** to verify that the token has the correct permissions.

**This does not stop or restart the kiln.** It does not affect the
20-minute GitHub status timer or the manual-start requirement.

## Check it is working

```bash
systemctl list-timers --all kiln-plot-publisher.timer
sudo journalctl -u kiln-plot-publisher.service -n 70 --no-pager
```


The timer runs at **00:00 and 12:00 UTC** (with up to a few minutes of
systemd scheduling tolerance). `Persistent=true` catches a missed
scheduled run after the Pi returns, assuming systemd and networking work.
Running with no CSV logs is not an error: it publishes empty plots labelled
as missing data rather than inventing readings.

Manually request another publication (and another Git commit):

```bash
sudo systemctl start kiln-plot-publisher.service
```


To test PNG rendering **without contacting GitHub**:

```bash
/usr/bin/python3 scripts/publish_kiln_plots.py --render-only
ls -lh logs/plot-cache/
```


To stop or resume **only** plot publication:

```bash
sudo systemctl disable --now kiln-plot-publisher.timer
sudo systemctl enable --now kiln-plot-publisher.timer
```


## How it works (and why it does not break Git pull)

1. The C++ controller continues writing one `logs/kiln_*.csv` per run
   with the existing columns: UTC timestamp, kiln temperature, humidity,
   reading age, heater/humidifier/fan commands, sensor fault and Pi CPU temp.
2. The plot generator streams relevant rows from those files and bins the last
   24 hours into 2-minute buckets, and 7 days into 15-minute buckets.
3. Matplotlib renders eight PNGs into `logs/plot-cache/` (ignored by
   Git). It also writes the current UTC generation timestamp into metadata.
4. The publisher uploads each PNG as a Git blob, creates a single Git tree
   based on the current `main` branch, creates one commit, and advances
   `main` via GitHub's API (without force). If somebody commits
   to main concurrently, it retries rather than overwriting their history.
5. **The Pi's checked-out Git working tree is left untouched.** Neither its
   locally edited kiln config nor its current source files are staged or
   committed. Future `git pull --ff-only` should not be blocked by
   untracked/local chart files.

The eight paths are always overwritten, never appended with a new filename.
Git stores old image versions in commit history; no cleanup is attempted.

### Important limitations

- **No live/instantaneous plotting**: charts only refresh every 12 hours and
  can be stale between updates.
- The Pi must be running and online long enough to generate and upload charts.
  An offline Pi cannot publish until it reconnects.
- GitHub token Contents permission is essential. If the token only has Issues
  permission, the service will fail with a permission error; its next timer
  attempt will retry, and the kiln controller is unaffected.
- Direct committing to main requires GitHub branch permissions allowing that.
- Pi **power on/off cannot be proven** from the existing CSV. The charts mark
  intervals with observed CSV logging and intervals without records. Missing
  records are not automatically treated as actual Pi power loss.
- The relay chart is based on software *commanded* states, not electrical
  measurement of heater power or relay contacts.
- An hourly/weekly average from a bucket with a few samples is based on
  those samples, not full coverage of the entire bucket.
- Charts with gaps/absence of data are not proof that physical heating stopped.
  Physical overtemperature protection remains essential.

# Plant Monitor

A small Home Assistant companion service for plant monitoring.

It watches Home Assistant plant sensor entities, keeps a small rolling state
file, sends readable condition-based notifications, and only waters after an
explicit confirmation action.

## Usage

Install locally:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
source .venv/bin/activate
plant --help
```

Or use the local install helper:

```bash
scripts/install_local.sh
```

Fill in `.env` with:

- `HA_URL`
- `HA_LONG_LIVED_TOKEN`
- `HOMELAB_FUNCTIONS_URL`
- `HOMELAB_FUNCTIONS_TOKEN`
- `HA_PLANTS_DASHBOARD_URL`

Typical first run:

```bash
# Writes plants.discovered.yaml by default. Review it before replacing plants.yaml.
plant discover

plant status
plant status --notify
plant monitor
```

## CLI

`plant discover`

Discovers live Home Assistant `plant.*` entities and proposes a clean
`plants.discovered.yaml` file. Use `--write` when you want discovery to replace
the configured `plants.yaml`.

`plant status`

Prints the current plant status as a color-coded table. Add `--notify` to send a
one-time notification digest through homelab-functions.

`plant monitor`

Runs the long-lived monitor. It listens for Home Assistant state changes,
sends individual plant alerts, handles notification actions, and runs the weekly
digest.

## Build

Build a local Docker image:

```bash
scripts/build_image.sh
```

Docker registry publishing is intentionally not wired yet. The deployment flow
will be updated once the target registry is available.

## Docs

### Configuration

Local-only files are ignored by git:

- `.env`
- `plants.yaml`
- `plants.discovered.yaml`
- `data/`

Use `.env.example` as the environment template. `plants.yaml` is generated from
Home Assistant discovery and is intentionally ignored by git.

### Data Model

Each plant is one object in `plants.yaml`:

- `plant_entity`: canonical Home Assistant `plant.*` entity
- `sensors`: raw moisture, temperature, battery, brightness, etc.
- `watering`: optional switch plus duration/cooldown
- `thresholds`: optional per-plant overrides
- `species`: selects fallback thresholds when Home Assistant does not expose
  plant-specific threshold data

Species defaults live in `plant_monitor/thresholds.py`.

### Notifications

Individual alerts are sent when a plant becomes orange/red or watering is
recommended after the condition hold window passes. Repeated alerts use a
backoff controlled by `ALERT_REPEAT_HOURS`. Delivery goes through
`homelab-functions`; this service still uses Home Assistant credentials for
state listening and pump control.

Notification actions:

- `Open Plants`: opens `HA_PLANTS_DASHBOARD_URL`
- `Delay 24h`: suppresses individual alerts for that plant
- watering confirmation appears only when watering is recommended and a watering
  switch is configured

### Thresholds and Automation

- Raw Home Assistant events update rolling sample and condition state
- Phone alerts fire on sustained condition transitions, severity escalation, or
  the repeat cadence
- Moisture low hold windows are species-specific: Boston fern 8h red, ficus 24h
  red, pothos 48h red, peperomia 72h red
- Wet/soggy soil is treated as a diagnostic condition and only phone-alerts
  after 72h when very wet
- Mild temperature and humidity issues are digest-first; hard temperature
  extremes can alert after 2h
- Moisture/temperature/humidity stale warning: 12 hours, digest-only
- Moisture/temperature/humidity stale red: 24 hours
- Battery stale warning: 5 days
- Battery stale red: 10 days
- Battery warning is digest-only; critical battery can phone-alert
- Watering is never automatic
- Watering is blocked if the moisture sensor is stale, the pump is missing, the
  pump cooldown is active, or the requested run time exceeds the configured cap
- After watering, dry alerts are suppressed for 4h and wet alerts for 6h

Optional LLM alert wording:

- `LLM_NOTIFICATION_TEXT=true`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

The LLM can only rewrite notification text. Severity, buttons, tags, URLs,
watering eligibility, and alert timing remain deterministic.

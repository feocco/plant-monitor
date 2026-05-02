# Plant Monitor

A small Home Assistant companion service for plant monitoring.

It uses Home Assistant `plant.*` entities as the source of truth, watches the raw
sensor entities for freshness, sends readable notifications, and only waters
after an explicit confirmation action.

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
- `HA_NOTIFY_SERVICE`
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
one-time Home Assistant notification digest.

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
recommended. Repeated alerts use a backoff controlled by `ALERT_REPEAT_HOURS`.

Notification actions:

- `Open Plants`: opens `HA_PLANTS_DASHBOARD_URL`
- `Delay 24h`: suppresses individual alerts for that plant
- watering confirmation appears only when watering is recommended and a watering
  switch is configured

### Thresholds and Automation

- Moisture/temperature/humidity stale warning: 12 hours
- Moisture/temperature/humidity stale red: 24 hours
- Battery stale warning: 5 days
- Battery stale red: 10 days
- Watering is never automatic
- Watering is blocked if the moisture sensor is stale, the pump is missing, the
  pump cooldown is active, or the requested run time exceeds the configured cap

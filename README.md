# Plant Monitor

A small Home Assistant companion service for plant monitoring.

The service reads plant state from Home Assistant, keeps recent local state,
turns sustained problems into plant conditions, sends quiet phone
notifications, and only runs watering pumps after an explicit confirmation.

## Quick Start

Install locally:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
source .venv/bin/activate
plant --help
```

Or use the helper:

```bash
scripts/install_local.sh
```

Create `.env` from `.env.example`, then fill in at least:

```bash
HA_URL=http://homeassistant.local:8123
HA_LONG_LIVED_TOKEN=replace_me
HOMELAB_FUNCTIONS_URL=http://homelab-functions:8091
HOMELAB_FUNCTIONS_TOKEN=replace_me
HA_PLANTS_DASHBOARD_URL=/lovelace/plants
```

Typical first run:

```bash
# Writes plants.discovered.yaml by default.
# Review it before replacing plants.yaml.
plant discover

plant status
plant status --notify
plant monitor
```

## Configuration

Local runtime files are intentionally ignored by git:

- `.env`
- `plants.yaml`
- `plants.discovered.yaml`
- `data/`

Environment configuration lives in `.env`. The main values are:

- `HA_URL`, `HA_LONG_LIVED_TOKEN`: Home Assistant connection.
- `HOMELAB_FUNCTIONS_URL`, `HOMELAB_FUNCTIONS_TOKEN`: phone notification service.
- `HA_PLANTS_DASHBOARD_URL`: target opened from notification actions.
- `CONFIG_PATH`: plant config path, default `plants.yaml`.
- `STATE_PATH`: runtime state path, default `data/state.json`.
- `SERVICE_HOST`, `SERVICE_PORT`, `SERVICE_CALLBACK_TOKEN`: callback and health server.
- `ALERT_REPEAT_HOURS`: repeat cadence for active phone-alert conditions.
- `ALERT_SNOOZE_HOURS`: delay duration for the notification snooze action.
- `DRY_RUN`: connect and evaluate without recording real alert sends.

Optional LLM text rewriting:

- `LLM_NOTIFICATION_TEXT=true`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

The LLM only rewrites message text. Rule decisions, severity, alert timing,
watering eligibility, tags, URLs, and buttons remain deterministic.

## Plant Config

Each plant is one object in `plants.yaml`:

```yaml
plants:
  - id: ficus_altissima_sun_room
    name: Ficus Altissima
    location: Sun room
    species: ficus_altissima
    plant_entity: plant.ficus_altissma_sun_room
    sensors:
      moisture: sensor.ficus_altissma_sun_room_moisture
      temperature: sensor.ficus_altissma_sun_room_temperature
      humidity: sensor.ficus_altissma_sun_room_humidity
      battery: sensor.ficus_altissma_sun_room_battery
      brightness: sensor.ficus_altissma_sun_room_illuminance
    watering:
      switch: switch.back_sun_room_watering_kit
      max_seconds: 10
      cooldown_hours: 48
    thresholds:
      moisture:
        min_green: 25
        min_orange: 15
      battery:
        orange: 30
        red: 15
```

Discovery reads live Home Assistant `plant.*` entities and related sensors. It
writes a proposed config, but the reviewed `plants.yaml` is the source of truth
for normal runs.

Threshold sources, in order:

1. Per-plant overrides in `plants.yaml`.
2. Species defaults in `plant_monitor/thresholds.py`.
3. Conservative fallback defaults.

## Running

Print current status:

```bash
plant status
```

Send a one-time digest notification:

```bash
plant status --notify
```

Run the long-lived service:

```bash
plant monitor
```

Run with Docker Compose:

```bash
docker compose up -d --build
docker logs -f plant-monitor
```

Build only:

```bash
scripts/build_image.sh
```

Images are published to GHCR by `.github/workflows/container.yml` when `main` is
pushed. NAS runtime config and deployment live outside this repo in
`homelab-config`.

## Architecture

High-level flow:

```text
Home Assistant WebSocket
  -> plant_monitor.ha
  -> PlantMonitor state cache
  -> condition_engine records samples and sustained conditions
  -> plant statuses
  -> NotificationPlanner decides what is due
  -> Notifier sends phone notifications through homelab-functions
```

The service uses Home Assistant WebSocket events for live updates and performs
periodic `get_states` reconciliation so missed events do not permanently stale
the local view.

Phone alerts are intentionally quiet:

- Raw readings create condition candidates immediately.
- Candidates become active only after their hold window passes.
- Notifications send on activation, repeat cadence, or severity-relevant active
  conditions.
- Numeric drift inside the same active condition does not keep retriggering.
- Lower-priority observations can appear in the weekly digest without becoming
  immediate phone alerts.

Watering is guarded:

- Watering is never automatic.
- A water button appears only when an active moisture-low condition is high
  confidence and the watering guard passes.
- Watering is blocked for stale moisture data, missing pump mapping, active pump
  cooldown, or a duration above the configured cap.
- After watering, dry alerts are suppressed briefly and wet/soggy observations
  are suppressed longer.
- The service schedules 1-hour and 4-hour lookbacks to report whether moisture
  or humidity changed after watering.

## Components

- `plant_monitor/cli.py`: `plant discover`, `plant status`, `plant monitor`.
- `plant_monitor/config.py`: `.env` and `plants.yaml` loading.
- `plant_monitor/discovery.py`: live Home Assistant discovery.
- `plant_monitor/ha.py`: Home Assistant WebSocket client wrapper.
- `plant_monitor/monitor.py`: long-running orchestration, reconnects, loops, event handling.
- `plant_monitor/condition_engine.py`: samples, hold windows, active condition lifecycle.
- `plant_monitor/policy.py`: shared threshold and sensor helpers.
- `plant_monitor/thresholds.py`: species defaults.
- `plant_monitor/notification_planner.py`: decides which active conditions should notify now.
- `plant_monitor/notify.py`: digest and phone notification formatting/sending.
- `plant_monitor/watering.py`: watering guard, pump execution, watering lookbacks.
- `plant_monitor/web.py`: health and callback server.
- `plant_monitor/runtime_state.py`: persisted `data/state.json`.

## Tests

Run the test suite:

```bash
.venv/bin/python -m pytest -q
```

Useful smoke checks before deploying:

```bash
plant status
docker build -t plant-monitor:local .
```

# Configuration

Plant Monitor uses two local configuration files:

- `.env` for service/runtime settings.
- `plants.yaml` for reviewed plant mappings and thresholds.

Both files are ignored by git. Use `.env.example` and discovery output as
templates, then keep real values local or in the NAS deployment config.

## Environment

Required:

```bash
HA_URL=http://homeassistant.local:8123
HA_LONG_LIVED_TOKEN=replace_me
```

Notifications:

```bash
HOMELAB_FUNCTIONS_URL=http://homelab-functions:8091
HOMELAB_FUNCTIONS_TOKEN=replace_me
HA_PLANTS_DASHBOARD_URL=/lovelace/plants
```

Runtime paths and logging:

```bash
TZ=America/New_York
LOG_LEVEL=INFO
CONFIG_PATH=plants.yaml
STATE_PATH=data/state.json
DRY_RUN=false
```

Alert behavior:

```bash
ALERT_SNOOZE_HOURS=24
ALERT_REPEAT_HOURS=24
```

Callback and health server:

```bash
SERVICE_HOST=0.0.0.0
SERVICE_PORT=8088
SERVICE_CALLBACK_TOKEN=replace_with_random_string
```

Optional LLM notification wording:

```bash
LLM_NOTIFICATION_TEXT=false
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
```

The LLM only rewrites notification text. Rule decisions, severity, alert timing,
watering eligibility, tags, URLs, and buttons remain deterministic.

## Plant Config

Each plant is one object in `plants.yaml`.

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

Common fields:

- `id`: stable slug used in state, notification tags, and watering callbacks.
- `name`, `location`: human-facing labels.
- `species`: selects defaults from `plant_monitor/thresholds.py`.
- `plant_entity`: canonical Home Assistant `plant.*` entity when available.
- `sensors`: raw Home Assistant entities used for freshness, trends, and rules.
- `watering`: optional pump switch, max duration, and cooldown.
- `thresholds`: optional per-plant overrides.

Threshold sources, in order:

1. Per-plant overrides in `plants.yaml`.
2. Species defaults in `plant_monitor/thresholds.py`.
3. Conservative fallback defaults.

## Discovery Workflow

```bash
plant discover
```

Discovery writes `plants.discovered.yaml` by default. Review the proposed
mappings before copying them into `plants.yaml`.

Use `--write` only when you intentionally want discovery to replace the
configured plant file:

```bash
plant discover --write
```

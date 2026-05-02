# Plant Monitor

A small Home Assistant companion service for low-maintenance plant monitoring.
It connects to Home Assistant over the WebSocket API, watches Zigbee plant sensor
updates, scores each plant as green/orange/red, sends Home Assistant
notifications, and only waters after an explicit guarded confirmation.

## Setup

1. Fill in `.env` with your Home Assistant URL and a long-lived access token.
   Set `HA_PLANTS_DASHBOARD_URL` to the dashboard path or full URL that should
   open when tapping plant notifications.
   Use `plants.example.yaml` as the starting point for your local `plants.yaml`.
2. Run discovery to propose entity mappings from live Home Assistant `plant.*`
   entities:

   ```bash
   python -m plant_monitor.discovery
   ```

3. Review `plants.discovered.yaml`. Discovery writes a review file by default so
   it does not overwrite your working config with uncertain matches.
4. Once it looks right, either edit `plants.yaml` manually or run:

   ```bash
   python -m plant_monitor.discovery --write
   ```

5. Run locally:

   ```bash
   python -m plant_monitor.main
   ```

6. To check the current status without starting the long-running monitor:

   ```bash
   python -m plant_monitor.status
   ```

7. To send a one-time test digest through Home Assistant notify:

   ```bash
   python -m plant_monitor.status --notify
   ```

8. Or run in Docker:

   ```bash
   docker compose up --build
   ```

## Build and Deploy

Build a local image:

```bash
scripts/build_image.sh
```

Deploy to a remote Docker server over SSH:

```bash
REMOTE_DOCKER_HOST=user@nas.local \
REMOTE_APP_DIR=/opt/plant-monitor \
scripts/deploy_remote.sh
```

The deploy script copies local `.env` and `plants.yaml` to the server so the
container can run, but those files are ignored by git and should not be pushed to
GitHub.

## Home Assistant Notes

The service uses the Home Assistant `plant` integration as the canonical source
for one clean object per plant. Raw sensors are attached to that object for
freshness checks and trend logic. The service uses `HA_NOTIFY_SERVICE` for
notifications, for example `notify.mobile_app_your_phone`. Watering buttons use
mobile app notification actions; tapping a watering action is detected from Home
Assistant's `mobile_app_notification_action` event stream. A small HTTP server
also exposes `/health` and guarded `/water/{plant_id}` endpoints for future
webhook-style use.

Individual alert notifications include an `Open Plants` action and a `Delay 24h`
action. The delay duration is controlled by `ALERT_SNOOZE_HOURS`. Alerts repeat
after `ALERT_REPEAT_HOURS` if the plant is still unhealthy, and normal numeric
sensor drift does not count as a new alert.

## Safety Defaults

- Stale warning after 12 hours without a sensor update.
- Stale red status after 24 hours without a sensor update.
- Watering is never automatic in v1.
- Watering confirmations are blocked if the moisture sensor is stale, the pump
  is missing, the pump cooldown is active, or the requested run time is too long.

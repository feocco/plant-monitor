# Plant Monitor

A small Home Assistant companion service for plant monitoring.

It reads plant state from Home Assistant, keeps recent local state, turns
sustained problems into plant conditions, sends quiet phone notifications, and
only runs watering pumps after explicit confirmation.

The service also exposes a small operational HTTP surface on port `8088`:
`/health`, `/docs`, `/openapi.json`, and the guarded watering callback
`/water/{plant_id}`.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
source .venv/bin/activate
plant --help
```

Or:

```bash
scripts/install_local.sh
```

Create `.env` from `.env.example`, then add the Home Assistant and notification
values described in [Configuration](docs/configuration.md).

## Discovery

Discovery reads live Home Assistant `plant.*` entities and related sensors. It
also groups unmatched soil sensors by physical Home Assistant device and writes
sensor-only plant proposals.

```bash
# Writes plants.discovered.yaml by default.
plant discover
```

Review `plants.discovered.yaml` before replacing `plants.yaml`. Existing
reviewed plants are preserved and new sensor devices are appended as proposals.
The reviewed `plants.yaml` file is the source of truth for normal runs.

## Common Commands

```bash
plant status
plant status --notify
plant monitor
```

Build the local image:

```bash
scripts/build_image.sh
```

Run locally with Docker Compose:

```bash
docker compose up -d --build
docker logs -f plant-monitor
```

## Runtime Files

These are local/runtime files and are ignored by git:

- `.env`
- `plants.yaml`
- `plants.discovered.yaml`
- `data/`

NAS runtime configuration and deployment live outside this repo in
`homelab-config`.

## Docs

- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [Security](docs/security.md)

## Tests

```bash
.venv/bin/python -m pytest -q
```

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from plant_monitor.models import (
    EntityMap,
    PlantConfig,
    ServiceConfig,
    SpeciesThresholds,
    ThresholdRange,
    WateringConfig,
)


def load_service_config(env_path: str | None = None) -> ServiceConfig:
    load_dotenv(env_path)
    return ServiceConfig(
        ha_url=_required("HA_URL").rstrip("/"),
        ha_token=_required("HA_LONG_LIVED_TOKEN"),
        notify_service=os.getenv("HA_NOTIFY_SERVICE", "notify.notify"),
        plants_dashboard_url=os.getenv("HA_PLANTS_DASHBOARD_URL", "/lovelace/plants"),
        alert_snooze_hours=int(os.getenv("ALERT_SNOOZE_HOURS", "24")),
        alert_repeat_hours=int(os.getenv("ALERT_REPEAT_HOURS", "24")),
        config_path=os.getenv("CONFIG_PATH", "plants.yaml"),
        state_path=os.getenv("STATE_PATH", "data/state.json"),
        service_host=os.getenv("SERVICE_HOST", "0.0.0.0"),
        service_port=int(os.getenv("SERVICE_PORT", "8088")),
        callback_token=os.getenv("SERVICE_CALLBACK_TOKEN", ""),
        dry_run=os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes", "on"},
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        timezone=os.getenv("TZ", "America/New_York"),
    )


def load_plants(path: str | Path) -> list[PlantConfig]:
    with Path(path).open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    plants = raw.get("plants", [])
    if not isinstance(plants, list):
        raise ValueError("plants.yaml must contain a top-level 'plants' list")
    return [_parse_plant(item) for item in plants]


def _parse_plant(raw: dict[str, Any]) -> PlantConfig:
    entities = raw.get("sensors") or raw.get("entities") or {}
    watering = raw.get("watering") or {}
    pump = watering.get("switch") or entities.get("pump")
    plant = PlantConfig(
        id=str(raw["id"]),
        name=str(raw["name"]),
        location=str(raw.get("location", "")),
        species=str(raw.get("species", "default")),
        plant_entity=raw.get("plant_entity"),
        entities=EntityMap(
            moisture=entities.get("moisture"),
            temperature=entities.get("temperature"),
            humidity=entities.get("humidity"),
            battery=entities.get("battery"),
            conductivity=entities.get("conductivity"),
            brightness=entities.get("brightness"),
            pump=pump,
        ),
        thresholds=_parse_thresholds(raw.get("thresholds") or {}),
        watering=WateringConfig(
            switch=pump,
            max_seconds=int(watering.get("max_seconds", 8)),
            cooldown_hours=int(watering.get("cooldown_hours", 48)),
        ),
    )
    return _validate_plant(plant)


def _validate_plant(plant: PlantConfig) -> PlantConfig:
    if not plant.id.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"Plant id must be slug-like: {plant.id}")
    if plant.watering.max_seconds < 1 or plant.watering.max_seconds > 60:
        raise ValueError(f"{plant.id}: watering.max_seconds must be between 1 and 60")
    if plant.watering.cooldown_hours < 1:
        raise ValueError(f"{plant.id}: watering.cooldown_hours must be at least 1")
    return replace(plant)


def _parse_thresholds(raw: dict[str, Any]) -> SpeciesThresholds | None:
    if not raw:
        return None
    battery = raw.get("battery") or {}
    if not isinstance(battery, dict):
        battery = {"min": battery}
    return SpeciesThresholds(
        moisture=_parse_range(raw.get("moisture") or {}),
        temperature=_parse_range(raw.get("temperature") or {}),
        humidity=_parse_range(raw.get("humidity") or {}),
        conductivity=_parse_range(raw.get("conductivity") or {}),
        brightness=_parse_range(raw.get("brightness") or {}),
        battery_orange=float(battery.get("orange", battery.get("min", 30))),
        battery_red=float(battery.get("red", 15)),
    )


def _parse_range(raw: dict[str, Any]) -> ThresholdRange:
    if not isinstance(raw, dict):
        raw = {"min": raw}
    return ThresholdRange(
        min_green=_float_or_none(raw.get("min_green", raw.get("min"))),
        min_orange=_float_or_none(raw.get("min_orange", raw.get("red_below", raw.get("min")))),
        max_green=_float_or_none(raw.get("max_green", raw.get("max"))),
        max_orange=_float_or_none(raw.get("max_orange", raw.get("red_above", raw.get("max")))),
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def write_discovered_config(path: str | Path, plants: list[dict[str, Any]]) -> None:
    payload = {"plants": plants}
    with Path(path).open("w", encoding="utf-8") as file:
        yaml.safe_dump(payload, file, sort_keys=False)


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value

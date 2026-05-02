from __future__ import annotations

import argparse
import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from plant_monitor.config import load_service_config, write_discovered_config
from plant_monitor.ha import HomeAssistantClient
from plant_monitor.logging_config import setup_logging
from plant_monitor.models import EntityState, PlantConfig

LOGGER = logging.getLogger(__name__)
PLANT_DOMAIN = "plant."
SENSOR_DOMAINS = ("sensor.",)
SENSOR_KINDS = ("moisture", "temperature", "humidity", "battery", "conductivity", "brightness")
KIND_HINTS = {
    "moisture": ("moisture", "soil", "humidity"),
    "temperature": ("temperature", "temp"),
    "humidity": ("air_humidity", "ambient_humidity"),
    "battery": ("battery",),
    "conductivity": ("conductivity", "fertility"),
    "brightness": ("brightness", "illuminance", "light", "lux"),
}
SPECIES_HINTS = {
    "boston_fern": ("boston", "fern"),
    "ficus_altissima": ("ficus", "altissma", "altissima", "rubber"),
    "golden_pothos": ("pothos",),
    "peperomia_jelly": ("peperomia", "jelly"),
}

async def discover(output_path: str | None = None, write: bool = False) -> None:
    config = load_service_config()
    setup_logging(config.log_level)
    ha = HomeAssistantClient(config)
    await ha.connect()
    try:
        states = await ha.get_states()
    finally:
        await ha.close()

    plants = _discover_plants(states, _existing_plants(config.config_path))
    target = Path(output_path or (config.config_path if write else "plants.discovered.yaml"))
    write_discovered_config(target, plants)
    LOGGER.info("Wrote %s plant objects to %s", len(plants), target)
    if not write:
        LOGGER.info("Review %s, then rerun with --write to replace %s.", target, config.config_path)


def _discover_plants(
    states: dict[str, EntityState],
    existing_plants: dict[str, PlantConfig] | None = None,
) -> list[dict[str, Any]]:
    existing_plants = existing_plants or {}
    plant_states = sorted(
        (state for state in states.values() if state.entity_id.startswith(PLANT_DOMAIN)),
        key=lambda state: state.entity_id,
    )
    raw_sensors = [state for state in states.values() if state.entity_id.startswith(SENSOR_DOMAINS)]
    return [_plant_object(plant_state, raw_sensors, existing_plants.get(_plant_id(plant_state))) for plant_state in plant_states]


def _plant_object(
    plant_state: EntityState,
    raw_sensors: list[EntityState],
    existing_plant: PlantConfig | None,
) -> dict[str, Any]:
    plant_id = _plant_id(plant_state)
    text = _search_text(plant_state)
    species = _species_for(text)
    sensors = _sensors_for(plant_state, raw_sensors)
    watering = _watering_from_existing(existing_plant)
    payload: dict[str, Any] = {
        "id": plant_id,
        "plant_entity": plant_state.entity_id,
        "name": _display_name(plant_state, species),
        "location": _location_for(plant_id, text),
        "species": species,
    }
    if sensors:
        payload["sensors"] = sensors
    thresholds = _thresholds_for(plant_state)
    if thresholds:
        payload["thresholds"] = thresholds
    if watering:
        payload["watering"] = watering
    return payload


def _existing_plants(path: str) -> dict[str, PlantConfig]:
    try:
        from plant_monitor.config import load_plants

        return {plant.id: plant for plant in load_plants(path)}
    except FileNotFoundError:
        return {}


def _watering_from_existing(plant: PlantConfig | None) -> dict[str, Any] | None:
    if plant is None or not plant.watering.switch:
        return None
    return {
        "switch": plant.watering.switch,
        "max_seconds": plant.watering.max_seconds,
        "cooldown_hours": plant.watering.cooldown_hours,
    }


def _plant_id(plant_state: EntityState) -> str:
    return plant_state.entity_id.removeprefix(PLANT_DOMAIN)


def _sensors_for(plant_state: EntityState, raw_sensors: list[EntityState]) -> dict[str, str]:
    sensors: dict[str, str] = {}
    for kind in SENSOR_KINDS:
        direct = _entity_from_attributes(plant_state.attributes, kind)
        if direct:
            sensors[kind] = direct
            continue
        inferred = _infer_sensor(plant_state, raw_sensors, kind)
        if inferred:
            sensors[kind] = inferred.entity_id
    return sensors


def _entity_from_attributes(attributes: dict[str, Any], kind: str) -> str | None:
    candidate_keys = (
        kind,
        f"{kind}_sensor",
        f"{kind}_entity",
        f"{kind}_entity_id",
    )
    for key in candidate_keys:
        value = attributes.get(key)
        if isinstance(value, str) and _looks_like_entity_id(value):
            return value
    sensors = attributes.get("sensors")
    if isinstance(sensors, dict):
        value = sensors.get(kind)
        if isinstance(value, str) and _looks_like_entity_id(value):
            return value
    return None


def _infer_sensor(plant_state: EntityState, raw_sensors: list[EntityState], kind: str) -> EntityState | None:
    plant_tokens = _meaningful_tokens(plant_state.entity_id, plant_state.attributes.get("friendly_name", ""))
    hints = KIND_HINTS[kind]
    scored: list[tuple[int, EntityState]] = []
    for sensor in raw_sensors:
        text = _search_text(sensor)
        if not any(hint in text for hint in hints):
            continue
        score = sum(3 for token in plant_tokens if token in text)
        score += sum(1 for hint in hints if hint in text)
        if score > 2:
            scored.append((score, sensor))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].entity_id))
    return scored[0][1]


def _thresholds_for(plant_state: EntityState) -> dict[str, Any]:
    attrs = plant_state.attributes
    thresholds: dict[str, Any] = {}
    for kind in ("moisture", "temperature", "conductivity", "brightness"):
        values = _range_threshold(attrs, kind)
        if values:
            thresholds[kind] = values
    battery_min = _number(attrs.get("min_battery"))
    if battery_min is not None:
        thresholds["battery"] = {"min": battery_min}
    return thresholds


def _range_threshold(attributes: dict[str, Any], kind: str) -> dict[str, float]:
    values: dict[str, float] = {}
    minimum = _number(attributes.get(f"min_{kind}"))
    maximum = _number(attributes.get(f"max_{kind}"))
    if minimum is not None:
        values["min"] = minimum
    if maximum is not None:
        values["max"] = maximum
    return values


def _species_for(text: str) -> str:
    for species, hints in SPECIES_HINTS.items():
        if any(hint in text for hint in hints):
            return species
    return "default"


def _display_name(plant_state: EntityState, species: str) -> str:
    friendly = plant_state.attributes.get("friendly_name")
    plant_id = plant_state.entity_id.removeprefix(PLANT_DOMAIN)
    if isinstance(friendly, str) and friendly and friendly != plant_id:
        return friendly
    if species != "default":
        return species.replace("_", " ").title()
    return plant_id.replace("_", " ").title()


def _location_for(plant_id: str, text: str) -> str:
    if "sun" in text:
        return "Sun room"
    if "office" in text and "hanging" in text:
        return "Office hanging"
    if "office" in text and "shelf" in text:
        return "Office shelf"
    if "office" in text:
        return "Office"
    if "hanging" in text:
        return "Hanging planter"
    return plant_id.replace("_", " ").title()


def _search_text(state: EntityState) -> str:
    friendly = str(state.attributes.get("friendly_name", ""))
    return f"{state.entity_id} {friendly}".lower()


def _meaningful_tokens(*values: object) -> set[str]:
    text = " ".join(str(value).lower() for value in values)
    tokens = set(re.findall(r"[a-z0-9]+", text))
    return tokens - {"plant", "sensor", "soil", "golden", "the", "and", "room"}


def _looks_like_entity_id(value: str) -> bool:
    return "." in value and not value.startswith("plant.")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Home Assistant plant objects.")
    parser.add_argument("--output", help="Output YAML path. Defaults to plants.discovered.yaml.")
    parser.add_argument("--write", action="store_true", help="Write directly to CONFIG_PATH/plants.yaml.")
    args = parser.parse_args()
    asyncio.run(discover(output_path=args.output, write=args.write))


if __name__ == "__main__":
    main()

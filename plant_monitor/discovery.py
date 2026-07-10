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
from plant_monitor.models import EntityState, PlantConfig, SpeciesThresholds

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
    "wandering_dude": ("wandering dude", "tradescantia", "wandering"),
}

async def discover(output_path: str | None = None, write: bool = False) -> None:
    config = load_service_config()
    setup_logging(config.log_level)
    ha = HomeAssistantClient(config)
    await ha.connect()
    try:
        states = await ha.get_states()
        entity_registry = _response_result(
            await ha.request({"type": "config/entity_registry/list"})
        )
        device_registry = _response_result(
            await ha.request({"type": "config/device_registry/list"})
        )
        area_registry = _response_result(
            await ha.request({"type": "config/area_registry/list"})
        )
    finally:
        await ha.close()

    plants = _discover_plants(
        states,
        _existing_plants(config.config_path),
        entity_registry=entity_registry,
        device_registry=device_registry,
        area_registry=area_registry,
    )
    target = Path(output_path or (config.config_path if write else "plants.discovered.yaml"))
    write_discovered_config(target, plants)
    LOGGER.info("Wrote %s plant objects to %s", len(plants), target)
    if not write:
        LOGGER.info("Review %s, then rerun with --write to replace %s.", target, config.config_path)


def _discover_plants(
    states: dict[str, EntityState],
    existing_plants: dict[str, PlantConfig] | None = None,
    *,
    entity_registry: list[dict[str, Any]] | None = None,
    device_registry: list[dict[str, Any]] | None = None,
    area_registry: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    existing_plants = existing_plants or {}
    plant_states = sorted(
        (state for state in states.values() if state.entity_id.startswith(PLANT_DOMAIN)),
        key=lambda state: state.entity_id,
    )
    raw_sensors = [state for state in states.values() if state.entity_id.startswith(SENSOR_DOMAINS)]
    plants = [_plant_config_payload(plant) for plant in existing_plants.values()]
    plants.extend(
        _plant_object(plant_state, raw_sensors)
        for plant_state in plant_states
        if _plant_id(plant_state) not in existing_plants
    )
    if entity_registry is None or device_registry is None:
        return plants
    plants.extend(
        _sensor_only_plants(
            states,
            existing_plants,
            plants,
            entity_registry,
            device_registry,
            area_registry or [],
        )
    )
    return plants


def _sensor_only_plants(
    states: dict[str, EntityState],
    existing_plants: dict[str, PlantConfig],
    discovered_plants: list[dict[str, Any]],
    entity_registry: list[dict[str, Any]],
    device_registry: list[dict[str, Any]],
    area_registry: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entity_rows = {
        row.get("entity_id"): row
        for row in entity_registry
        if isinstance(row.get("entity_id"), str)
    }
    entities_by_device: dict[str, list[dict[str, Any]]] = {}
    for row in entity_registry:
        device_id = row.get("device_id")
        if not isinstance(device_id, str) or row.get("disabled_by") is not None:
            continue
        entities_by_device.setdefault(device_id, []).append(row)

    claimed_entities = _configured_entity_ids(existing_plants.values())
    for plant in discovered_plants:
        claimed_entities.update((plant.get("sensors") or {}).values())
        plant_entity = plant.get("plant_entity")
        if isinstance(plant_entity, str):
            claimed_entities.add(plant_entity)
    claimed_devices = {
        entity_rows[entity_id].get("device_id")
        for entity_id in claimed_entities
        if entity_id in entity_rows
    }
    area_names = {
        row.get("area_id"): row.get("name")
        for row in area_registry
        if isinstance(row.get("area_id"), str)
    }

    proposals: list[dict[str, Any]] = []
    for device in device_registry:
        device_id = device.get("id")
        if not isinstance(device_id, str) or device_id in claimed_devices:
            continue
        rows = entities_by_device.get(device_id, [])
        moisture = _device_sensor(rows, states, "moisture")
        if moisture is None:
            continue
        device_name = str(device.get("name_by_user") or device.get("name") or device_id)
        name = _clean_sensor_name(device_name)
        location = str(area_names.get(device.get("area_id")) or "Unknown")
        sensors = {"moisture": moisture}
        for kind in ("temperature", "battery"):
            entity_id = _device_sensor(rows, states, kind)
            if entity_id:
                sensors[kind] = entity_id
        proposals.append(
            {
                "id": _sensor_plant_id(name, location),
                "plant_entity": None,
                "name": name,
                "location": location,
                "species": _species_for(f"{name} {location}".lower()),
                "sensors": sensors,
            }
        )
    return sorted(proposals, key=lambda plant: plant["id"])


def _configured_entity_ids(plants: object) -> set[str]:
    entity_ids: set[str] = set()
    for plant in plants:
        if plant.plant_entity:
            entity_ids.add(plant.plant_entity)
        for entity_id in vars(plant.entities).values():
            if entity_id:
                entity_ids.add(entity_id)
    return entity_ids


def _device_sensor(
    rows: list[dict[str, Any]],
    states: dict[str, EntityState],
    kind: str,
) -> str | None:
    candidates: list[tuple[int, str]] = []
    for row in rows:
        entity_id = row.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id.startswith("sensor."):
            continue
        state = states.get(entity_id)
        if state is None:
            continue
        device_class = str(state.attributes.get("device_class") or "")
        original_name = str(row.get("original_name") or "").lower()
        if kind == "moisture" and device_class == "moisture":
            preferred = 0 if "soil_moisture" in entity_id or original_name == "soil moisture" else 1
            candidates.append((preferred, entity_id))
        elif kind == "temperature" and device_class == "temperature":
            candidates.append((0, entity_id))
        elif kind == "battery" and device_class == "battery":
            candidates.append((0, entity_id))
    if not candidates:
        return None
    return min(candidates)[1]


def _clean_sensor_name(value: str) -> str:
    return re.sub(r"\s+soil sensor$", "", value, flags=re.IGNORECASE).strip()


def _sensor_plant_id(name: str, location: str) -> str:
    name_slug = _slug(name)
    location_slug = _slug(location)
    if location_slug and location_slug not in name_slug:
        return f"{location_slug}_{name_slug}"
    return name_slug


def _slug(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")


def _response_result(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response.get("result", response)
    if not isinstance(result, list):
        raise ValueError("Home Assistant registry response was not a list")
    return result


def _plant_object(
    plant_state: EntityState,
    raw_sensors: list[EntityState],
) -> dict[str, Any]:
    plant_id = _plant_id(plant_state)
    text = _search_text(plant_state)
    species = _species_for(text)
    sensors = _sensors_for(plant_state, raw_sensors)
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
    return payload


def _plant_config_payload(plant: PlantConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": plant.id,
        "plant_entity": plant.plant_entity,
        "name": plant.name,
        "location": plant.location,
        "species": plant.species,
    }
    sensors = {
        kind: entity_id
        for kind, entity_id in (
            ("moisture", plant.entities.moisture),
            ("temperature", plant.entities.temperature),
            ("humidity", plant.entities.humidity),
            ("battery", plant.entities.battery),
            ("conductivity", plant.entities.conductivity),
            ("brightness", plant.entities.brightness),
        )
        if entity_id
    }
    if sensors:
        payload["sensors"] = sensors
    watering = _watering_from_existing(plant)
    if watering:
        payload["watering"] = watering
    if plant.thresholds is not None:
        payload["thresholds"] = _threshold_config(plant.thresholds)
    return payload


def _threshold_config(thresholds: SpeciesThresholds) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for kind in ("moisture", "temperature", "humidity", "conductivity", "brightness"):
        threshold_range = getattr(thresholds, kind)
        values = {
            field: value
            for field, value in (
                ("min_green", threshold_range.min_green),
                ("min_orange", threshold_range.min_orange),
                ("max_green", threshold_range.max_green),
                ("max_orange", threshold_range.max_orange),
            )
            if value is not None
        }
        if values:
            payload[kind] = values
    payload["battery"] = {
        "orange": thresholds.battery_orange,
        "red": thresholds.battery_red,
    }
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

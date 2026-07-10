from __future__ import annotations

from datetime import UTC, datetime

from plant_monitor.discovery import _discover_plants
from plant_monitor.models import EntityMap, EntityState, PlantConfig, WateringConfig

NOW = datetime(2026, 5, 2, 16, 0, tzinfo=UTC)


def test_discovery_uses_plant_entities_as_roots_and_ignores_noise() -> None:
    states = {
        "plant.office_golden_pothos": _state(
            "plant.office_golden_pothos",
            "ok",
            {"friendly_name": "Golden Pothos Office Shelf", "min_moisture": 20, "max_moisture": 60},
        ),
        "sensor.office_golden_pothos_moisture": _state(
            "sensor.office_golden_pothos_moisture",
            "41",
            {"friendly_name": "Soil Sensor Pothos Office Humidity"},
        ),
        "switch.office_watering_kit": _state("switch.office_watering_kit", "off", {"friendly_name": "Office Watering Kit"}),
        "update.office_golden_pothos_firmware": _state(
            "update.office_golden_pothos_firmware",
            "off",
            {"friendly_name": "Pothos Office Firmware"},
        ),
        "automation.notify_everyone_hanging_pothos_soil_humidity_low": _state(
            "automation.notify_everyone_hanging_pothos_soil_humidity_low",
            "on",
            {"friendly_name": "Notify Everyone Hanging Pothos Soil Humidity Low"},
        ),
    }

    plants = _discover_plants(states)

    assert len(plants) == 1
    assert plants[0]["id"] == "office_golden_pothos"
    assert plants[0]["plant_entity"] == "plant.office_golden_pothos"
    assert plants[0]["sensors"]["moisture"] == "sensor.office_golden_pothos_moisture"
    assert "watering" not in plants[0]
    assert plants[0]["thresholds"]["moisture"] == {"min": 20.0, "max": 60.0}


def test_discovery_preserves_watering_from_existing_local_config() -> None:
    states = {
        "plant.office_golden_pothos": _state(
            "plant.office_golden_pothos",
            "ok",
            {"friendly_name": "Golden Pothos Office Shelf"},
        ),
    }
    existing = {
        "office_golden_pothos": PlantConfig(
            id="office_golden_pothos",
            name="Reviewed Golden Pothos",
            location="Reviewed office shelf",
            species="golden_pothos",
            plant_entity="plant.office_golden_pothos",
            entities=EntityMap(moisture="sensor.reviewed_moisture"),
            watering=WateringConfig(switch="switch.office_watering_kit", max_seconds=9, cooldown_hours=48),
        )
    }

    plants = _discover_plants(states, existing)

    assert plants[0]["watering"]["switch"] == "switch.office_watering_kit"
    assert plants[0]["watering"]["max_seconds"] == 9
    assert plants[0]["name"] == "Reviewed Golden Pothos"
    assert plants[0]["location"] == "Reviewed office shelf"
    assert plants[0]["sensors"] == {"moisture": "sensor.reviewed_moisture"}


def test_discovery_proposes_sensor_only_plant_from_device_registry() -> None:
    states = {
        "sensor.tinker_battery": _state(
            "sensor.tinker_battery", "100", {"device_class": "battery"}
        ),
        "sensor.tinker_moisture": _state(
            "sensor.tinker_moisture", "69", {"device_class": "moisture"}
        ),
        "sensor.tinker_soil_moisture": _state(
            "sensor.tinker_soil_moisture", "69", {"device_class": "moisture"}
        ),
        "sensor.tinker_temperature": _state(
            "sensor.tinker_temperature", "72", {"device_class": "temperature"}
        ),
    }
    entity_registry = [
        _registry_entity("sensor.tinker_battery", "device-tinker", "Battery"),
        _registry_entity("sensor.tinker_moisture", "device-tinker", "Moisture"),
        _registry_entity(
            "sensor.tinker_soil_moisture", "device-tinker", "Soil moisture"
        ),
        _registry_entity("sensor.tinker_temperature", "device-tinker", "Temperature"),
    ]
    device_registry = [
        {
            "id": "device-tinker",
            "name_by_user": "Tinker Wandering Dude Soil Sensor",
            "name": "Third Reality Soil Sensor",
            "area_id": "back_sun_room",
        }
    ]
    area_registry = [{"area_id": "back_sun_room", "name": "Back Sun Room"}]

    plants = _discover_plants(
        states,
        entity_registry=entity_registry,
        device_registry=device_registry,
        area_registry=area_registry,
    )

    assert plants == [
        {
            "id": "back_sun_room_tinker_wandering_dude",
            "plant_entity": None,
            "name": "Tinker Wandering Dude",
            "location": "Back Sun Room",
            "species": "wandering_dude",
            "sensors": {
                "moisture": "sensor.tinker_soil_moisture",
                "temperature": "sensor.tinker_temperature",
                "battery": "sensor.tinker_battery",
            },
        }
    ]


def test_discovery_preserves_sensor_only_plant_already_in_reviewed_config() -> None:
    states = {
        "sensor.existing_soil_moisture": _state(
            "sensor.existing_soil_moisture", "55", {"device_class": "moisture"}
        ),
        "sensor.existing_battery": _state(
            "sensor.existing_battery", "90", {"device_class": "battery"}
        ),
    }
    existing = {
        "existing_plant": PlantConfig(
            id="existing_plant",
            name="Existing Plant",
            location="Office",
            species="golden_pothos",
            plant_entity=None,
            entities=EntityMap(moisture="sensor.existing_soil_moisture"),
        )
    }

    plants = _discover_plants(
        states,
        existing,
        entity_registry=[
            _registry_entity(
                "sensor.existing_soil_moisture", "device-existing", "Soil moisture"
            ),
            _registry_entity("sensor.existing_battery", "device-existing", "Battery"),
        ],
        device_registry=[
            {
                "id": "device-existing",
                "name": "Existing Plant Soil Sensor",
                "area_id": "office",
            }
        ],
        area_registry=[{"area_id": "office", "name": "Office"}],
    )

    assert plants == [
        {
            "id": "existing_plant",
            "plant_entity": None,
            "name": "Existing Plant",
            "location": "Office",
            "species": "golden_pothos",
            "sensors": {"moisture": "sensor.existing_soil_moisture"},
        }
    ]


def _registry_entity(
    entity_id: str,
    device_id: str,
    original_name: str,
) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "device_id": device_id,
        "original_name": original_name,
        "disabled_by": None,
    }


def _state(entity_id: str, state: str, attributes: dict) -> EntityState:
    return EntityState(
        entity_id=entity_id,
        state=state,
        attributes=attributes,
        last_changed=NOW,
        last_updated=NOW,
    )

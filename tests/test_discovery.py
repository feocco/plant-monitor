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
            name="Golden Pothos",
            location="Office shelf",
            species="golden_pothos",
            plant_entity="plant.office_golden_pothos",
            entities=EntityMap(),
            watering=WateringConfig(switch="switch.office_watering_kit", max_seconds=9, cooldown_hours=48),
        )
    }

    plants = _discover_plants(states, existing)

    assert plants[0]["watering"]["switch"] == "switch.office_watering_kit"
    assert plants[0]["watering"]["max_seconds"] == 9


def _state(entity_id: str, state: str, attributes: dict) -> EntityState:
    return EntityState(
        entity_id=entity_id,
        state=state,
        attributes=attributes,
        last_changed=NOW,
        last_updated=NOW,
    )

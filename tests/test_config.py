from __future__ import annotations

from pathlib import Path

from plant_monitor.config import load_plants, load_service_config
from plant_monitor.thresholds import SPECIES_THRESHOLDS


def test_load_service_config_reads_homelab_functions_settings(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HA_URL", "http://homeassistant.local:8123/")
    monkeypatch.setenv("HA_LONG_LIVED_TOKEN", "ha-token")
    monkeypatch.setenv("HOMELAB_FUNCTIONS_URL", "http://homelab-functions:8091")
    monkeypatch.setenv("HOMELAB_FUNCTIONS_TOKEN", "functions-token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("LLM_NOTIFICATION_TEXT", "true")

    config = load_service_config(str(tmp_path / ".env"))

    assert config.ha_url == "http://homeassistant.local:8123"
    assert config.homelab_functions_url == "http://homelab-functions:8091"
    assert config.homelab_functions_token == "functions-token"
    assert config.openai_api_key == "sk-test"
    assert config.openai_model == "gpt-test"
    assert config.llm_notification_text


def test_load_plants_parses_entities_and_watering(tmp_path: Path) -> None:
    config = tmp_path / "plants.yaml"
    config.write_text(
        """
plants:
  - id: office_shelf_golden_pothos
    plant_entity: plant.office_shelf_golden_pothos
    name: Golden Pothos
    location: Office shelf
    species: golden_pothos
    sensors:
      moisture: sensor.moisture
      temperature: sensor.temperature
      humidity: sensor.humidity
      battery: sensor.battery
    thresholds:
      moisture:
        min: 20
        max: 60
      battery:
        min: 25
    watering:
      switch: switch.pump
      max_seconds: 9
      cooldown_hours: 36
""",
        encoding="utf-8",
    )

    plants = load_plants(config)

    assert len(plants) == 1
    assert plants[0].plant_entity == "plant.office_shelf_golden_pothos"
    assert plants[0].entities.moisture == "sensor.moisture"
    assert plants[0].entities.pump == "switch.pump"
    assert plants[0].thresholds is not None
    assert plants[0].thresholds.moisture.min_green == 20
    assert plants[0].watering.max_seconds == 9
    assert plants[0].watering.cooldown_hours == 36


def test_load_plants_parses_sensor_only_plant_with_blank_plant_entity(
    tmp_path: Path,
) -> None:
    config = tmp_path / "plants.yaml"
    config.write_text(
        """
plants:
  - id: front_porch_garden_box
    plant_entity:
    name: Front Porch Garden Box
    location: Front porch
    species: outdoor_mixed_vegetable_container
    sensors:
      moisture: sensor.porch_garden_1_soil_moisture
      battery: sensor.porch_garden_1_battery
""",
        encoding="utf-8",
    )

    plants = load_plants(config)

    assert len(plants) == 1
    assert plants[0].plant_entity is None
    assert plants[0].entities.moisture == "sensor.porch_garden_1_soil_moisture"
    assert plants[0].entities.battery == "sensor.porch_garden_1_battery"
    assert plants[0].entities.temperature is None


def test_outdoor_threshold_profiles_have_expected_moisture_bands() -> None:
    expected = {
        "outdoor_mixed_vegetable_container": (35, 25, 80, 90),
        "outdoor_mixed_annual_hanging_basket": (35, 25, 88, 95),
        "fuchsia_hanging_basket": (40, 30, 88, 95),
    }

    for species, bands in expected.items():
        moisture = SPECIES_THRESHOLDS[species].moisture
        assert (
            moisture.min_green,
            moisture.min_orange,
            moisture.max_green,
            moisture.max_orange,
        ) == bands

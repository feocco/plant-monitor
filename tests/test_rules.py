from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plant_monitor.models import EntityMap, EntityState, PlantConfig, Severity
from plant_monitor.rules import evaluate_plant, watering_decision

NOW = datetime(2026, 5, 2, 16, 0, tzinfo=UTC)


def test_green_status_when_values_are_fresh_and_in_range() -> None:
    plant = _plant()
    status = evaluate_plant(plant, _states(moisture=42, temperature=72, humidity=55, battery=88, plant="ok"), NOW)

    assert status.label == Severity.GREEN
    assert not status.issues
    assert not status.watering_recommended


def test_stale_sensor_is_orange_after_12_hours_and_red_after_24_hours() -> None:
    plant = _plant()
    orange_states = _states(moisture=42, temperature=72, humidity=55, battery=88, age_hours=13)
    red_states = _states(moisture=42, temperature=72, humidity=55, battery=88, age_hours=25)

    assert evaluate_plant(plant, orange_states, NOW).label == Severity.ORANGE
    assert evaluate_plant(plant, red_states, NOW).label == Severity.RED


def test_battery_freshness_uses_longer_stale_window() -> None:
    plant = _plant()
    one_day_old = _states(moisture=42, temperature=72, humidity=55, battery=88, battery_age_hours=25)
    five_days_old = _states(moisture=42, temperature=72, humidity=55, battery=88, battery_age_hours=121)
    ten_days_old = _states(moisture=42, temperature=72, humidity=55, battery=88, battery_age_hours=241)

    assert evaluate_plant(plant, one_day_old, NOW).label == Severity.GREEN
    assert evaluate_plant(plant, five_days_old, NOW).label == Severity.ORANGE
    assert evaluate_plant(plant, ten_days_old, NOW).label == Severity.RED


def test_low_battery_thresholds() -> None:
    plant = _plant()

    orange = evaluate_plant(plant, _states(moisture=42, temperature=72, humidity=55, battery=30), NOW)
    red = evaluate_plant(plant, _states(moisture=42, temperature=72, humidity=55, battery=15), NOW)

    assert orange.label == Severity.ORANGE
    assert any(issue.sensor == "battery" for issue in orange.issues)
    assert red.label == Severity.RED


def test_species_moisture_defaults_drive_watering_recommendation() -> None:
    plant = _plant(species="boston_fern")
    status = evaluate_plant(plant, _states(moisture=29, temperature=72, humidity=55, battery=88), NOW)

    assert status.label == Severity.RED
    assert status.watering_recommended


def test_plant_entity_problem_is_primary_health_signal() -> None:
    plant = _plant()
    status = evaluate_plant(
        plant,
        _states(moisture=42, temperature=72, humidity=55, battery=88, plant="problem"),
        NOW,
    )

    assert status.label == Severity.ORANGE
    assert any(issue.sensor == "plant" for issue in status.issues)


def test_watering_guard_blocks_stale_sensor() -> None:
    plant = _plant()
    decision = watering_decision(
        plant,
        _states(moisture=10, temperature=72, humidity=55, battery=88, age_hours=25),
        last_watered_at=None,
        now=NOW,
    )

    assert not decision.allowed
    assert any("stale" in reason for reason in decision.reasons)


def test_watering_guard_blocks_active_cooldown() -> None:
    plant = _plant()
    decision = watering_decision(
        plant,
        _states(moisture=10, temperature=72, humidity=55, battery=88),
        last_watered_at=NOW - timedelta(hours=2),
        now=NOW,
    )

    assert not decision.allowed
    assert any("cooldown" in reason for reason in decision.reasons)


def test_watering_guard_caps_duration_when_allowed() -> None:
    plant = _plant()
    decision = watering_decision(
        plant,
        _states(moisture=10, temperature=72, humidity=55, battery=88),
        last_watered_at=None,
        requested_seconds=999,
        now=NOW,
    )

    assert decision.allowed
    assert decision.seconds == plant.watering.max_seconds


def _plant(species: str = "golden_pothos") -> PlantConfig:
    return PlantConfig(
        id="office_shelf_golden_pothos",
        name="Golden Pothos",
        location="Office shelf",
        species=species,
        plant_entity="plant.office_shelf_golden_pothos",
        entities=EntityMap(
            moisture="sensor.moisture",
            temperature="sensor.temperature",
            humidity="sensor.humidity",
            battery="sensor.battery",
            pump="switch.office_watering_kit",
        ),
    )


def _states(
    moisture: float,
    temperature: float,
    humidity: float,
    battery: float,
    plant: str = "ok",
    age_hours: int = 1,
    battery_age_hours: int | None = None,
) -> dict[str, EntityState]:
    updated = NOW - timedelta(hours=age_hours)
    battery_updated = NOW - timedelta(hours=battery_age_hours or age_hours)
    return {
        "plant.office_shelf_golden_pothos": _state("plant.office_shelf_golden_pothos", plant, updated),
        "sensor.moisture": _state("sensor.moisture", moisture, updated),
        "sensor.temperature": _state("sensor.temperature", temperature, updated),
        "sensor.humidity": _state("sensor.humidity", humidity, updated),
        "sensor.battery": _state("sensor.battery", battery, battery_updated),
    }


def _state(entity_id: str, value: object, updated: datetime) -> EntityState:
    return EntityState(
        entity_id=entity_id,
        state=str(value),
        attributes={},
        last_changed=updated,
        last_updated=updated,
    )

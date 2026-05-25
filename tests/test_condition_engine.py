from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plant_monitor.condition_engine import (
    active_condition_records,
    due_phone_conditions,
    mark_notified,
    plant_statuses_from_conditions,
    update_conditions,
)
from plant_monitor.models import EntityMap, EntityState, PlantConfig, Severity
from plant_monitor.runtime_state import RuntimeState

NOW = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)


def test_condition_activates_only_after_hold_window() -> None:
    plant = _plant("ficus_altissima")
    runtime = RuntimeState()
    states = _states(moisture=20, updated=NOW)

    update_conditions([plant], states, runtime, NOW)

    assert active_condition_records(runtime) == []

    update_conditions(
        [plant],
        _states(moisture=20, updated=NOW + timedelta(hours=24)),
        runtime,
        NOW + timedelta(hours=24),
    )

    active = active_condition_records(runtime)
    assert [record.key for record in active] == [
        "plant_ficus_altissima:moisture_low:red"
    ]
    assert due_phone_conditions(runtime, plant.id, 24, NOW + timedelta(hours=24))


def test_transient_missing_sensor_does_not_activate_unavailable_alert() -> None:
    plant = _plant("golden_pothos")
    runtime = RuntimeState()
    missing_battery = _states(updated=NOW)
    missing_battery.pop("sensor.battery")

    update_conditions([plant], missing_battery, runtime, NOW)

    assert active_condition_records(runtime) == []

    update_conditions(
        [plant],
        _states(updated=NOW + timedelta(minutes=2)),
        runtime,
        NOW + timedelta(minutes=2),
    )

    assert active_condition_records(runtime) == []
    assert runtime.condition_records[
        "plant_golden_pothos:battery_unavailable:red"
    ].resolved_at


def test_missing_sensor_activates_unavailable_alert_after_hold_window() -> None:
    plant = _plant("golden_pothos")
    runtime = RuntimeState()
    missing_battery = _states(updated=NOW)
    missing_battery.pop("sensor.battery")

    update_conditions([plant], missing_battery, runtime, NOW)
    update_conditions([plant], missing_battery, runtime, NOW + timedelta(minutes=10))

    active = active_condition_records(runtime)
    assert [record.key for record in active] == [
        "plant_golden_pothos:battery_unavailable:red"
    ]
    assert due_phone_conditions(runtime, plant.id, 24, NOW + timedelta(minutes=10))


def test_active_condition_does_not_retrigger_for_numeric_drift_until_repeat() -> None:
    plant = _plant("ficus_altissima")
    runtime = RuntimeState()
    update_conditions([plant], _states(moisture=20, updated=NOW), runtime, NOW)
    update_conditions(
        [plant],
        _states(moisture=19, updated=NOW + timedelta(hours=24)),
        runtime,
        NOW + timedelta(hours=24),
    )
    due = due_phone_conditions(runtime, plant.id, 24, NOW + timedelta(hours=24))
    mark_notified(due, NOW + timedelta(hours=24))

    update_conditions(
        [plant],
        _states(moisture=18, updated=NOW + timedelta(hours=25)),
        runtime,
        NOW + timedelta(hours=25),
    )

    assert due_phone_conditions(runtime, plant.id, 24, NOW + timedelta(hours=25)) == []


def test_resolved_condition_can_alert_again_later() -> None:
    plant = _plant("ficus_altissima")
    runtime = RuntimeState()
    update_conditions([plant], _states(moisture=20, updated=NOW), runtime, NOW)
    update_conditions(
        [plant],
        _states(moisture=20, updated=NOW + timedelta(hours=24)),
        runtime,
        NOW + timedelta(hours=24),
    )
    due = due_phone_conditions(runtime, plant.id, 24, NOW + timedelta(hours=24))
    mark_notified(due, NOW + timedelta(hours=24))

    update_conditions(
        [plant],
        _states(moisture=40, updated=NOW + timedelta(hours=25)),
        runtime,
        NOW + timedelta(hours=25),
    )
    update_conditions(
        [plant],
        _states(moisture=20, updated=NOW + timedelta(hours=48)),
        runtime,
        NOW + timedelta(hours=48),
    )
    update_conditions(
        [plant],
        _states(moisture=20, updated=NOW + timedelta(hours=72)),
        runtime,
        NOW + timedelta(hours=72),
    )

    assert due_phone_conditions(runtime, plant.id, 24, NOW + timedelta(hours=72))


def test_plant_specific_moisture_windows() -> None:
    cases = {
        "boston_fern": 8,
        "ficus_altissima": 24,
        "golden_pothos": 48,
        "peperomia_jelly": 72,
    }
    for species, red_hours in cases.items():
        plant = _plant(species)
        runtime = RuntimeState()
        update_conditions([plant], _states(moisture=10, updated=NOW), runtime, NOW)
        update_conditions(
            [plant],
            _states(moisture=10, updated=NOW + timedelta(hours=red_hours - 1)),
            runtime,
            NOW + timedelta(hours=red_hours - 1),
        )
        assert active_condition_records(runtime) == []
        update_conditions(
            [plant],
            _states(moisture=10, updated=NOW + timedelta(hours=red_hours)),
            runtime,
            NOW + timedelta(hours=red_hours),
        )
        assert active_condition_records(runtime)


def test_watering_cooldown_blocks_water_button_after_dry_condition_activates() -> None:
    plant = _plant("boston_fern", pump=True)
    runtime = RuntimeState(last_watered_at={plant.id: NOW})

    update_conditions([plant], _states(moisture=10, updated=NOW), runtime, NOW)
    update_conditions(
        [plant],
        _states(moisture=10, updated=NOW + timedelta(hours=8)),
        runtime,
        NOW + timedelta(hours=8),
    )

    records = active_condition_records(runtime)
    statuses = plant_statuses_from_conditions([plant], records, {plant.id: False})

    assert statuses[0].label == Severity.RED
    assert not statuses[0].watering_recommended
    assert due_phone_conditions(runtime, plant.id, 24, NOW + timedelta(hours=8))


def test_sun_driven_heat_spike_is_digest_only() -> None:
    plant = _plant("golden_pothos", brightness=True)
    runtime = RuntimeState()
    states = _states(temperature=96, brightness=2500, updated=NOW)

    update_conditions([plant], states, runtime, NOW)
    update_conditions(
        [plant],
        _states(temperature=96, brightness=2500, updated=NOW + timedelta(hours=2)),
        runtime,
        NOW + timedelta(hours=2),
    )

    records = active_condition_records(runtime)
    assert records[0].kind == "temperature_extreme_high"
    assert not records[0].phone_alert


def test_sample_history_retains_fourteen_days() -> None:
    plant = _plant("golden_pothos")
    runtime = RuntimeState()
    old = NOW - timedelta(days=15)
    runtime.samples.append(
        runtime_sample(old, plant.id, "moisture", "sensor.moisture", 30)
    )

    update_conditions([plant], _states(moisture=28, updated=NOW), runtime, NOW)

    assert all(sample.timestamp >= NOW - timedelta(days=14) for sample in runtime.samples)
    assert any(sample.entity_id == "sensor.moisture" for sample in runtime.samples)


def _plant(
    species: str,
    *,
    pump: bool = False,
    brightness: bool = False,
) -> PlantConfig:
    return PlantConfig(
        id=f"plant_{species}",
        name=species,
        location="Office",
        species=species,
        plant_entity=None,
        entities=EntityMap(
            moisture="sensor.moisture",
            temperature="sensor.temperature",
            humidity="sensor.humidity",
            battery="sensor.battery",
            brightness="sensor.brightness" if brightness else None,
            pump="switch.pump" if pump else None,
        ),
    )


def _states(
    *,
    moisture: float = 40,
    temperature: float = 72,
    humidity: float = 55,
    battery: float = 88,
    brightness: float | None = None,
    updated: datetime = NOW,
) -> dict[str, EntityState]:
    states = {
        "sensor.moisture": _state("sensor.moisture", moisture, updated),
        "sensor.temperature": _state("sensor.temperature", temperature, updated),
        "sensor.humidity": _state("sensor.humidity", humidity, updated),
        "sensor.battery": _state("sensor.battery", battery, updated),
    }
    if brightness is not None:
        states["sensor.brightness"] = _state("sensor.brightness", brightness, updated)
    return states


def _state(entity_id: str, value: float, updated: datetime) -> EntityState:
    return EntityState(
        entity_id=entity_id,
        state=str(value),
        attributes={},
        last_changed=updated,
        last_updated=updated,
    )


def runtime_sample(timestamp, plant_id, sensor, entity_id, value):
    from plant_monitor.runtime_state import SensorSample

    return SensorSample(timestamp, plant_id, sensor, entity_id, value)

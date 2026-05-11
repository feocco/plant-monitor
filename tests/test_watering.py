from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plant_monitor.models import EntityMap, EntityState, PlantConfig
from plant_monitor.watering import watering_decision

NOW = datetime(2026, 5, 2, 16, 0, tzinfo=UTC)


def test_watering_guard_blocks_stale_sensor() -> None:
    plant = _plant()
    decision = watering_decision(
        plant,
        _states(moisture=10, age_hours=25),
        last_watered_at=None,
        now=NOW,
    )

    assert not decision.allowed
    assert any("stale" in reason for reason in decision.reasons)


def test_watering_guard_blocks_active_cooldown() -> None:
    plant = _plant()
    decision = watering_decision(
        plant,
        _states(moisture=10),
        last_watered_at=NOW - timedelta(hours=2),
        now=NOW,
    )

    assert not decision.allowed
    assert any("cooldown" in reason for reason in decision.reasons)


def test_watering_guard_caps_duration_when_allowed() -> None:
    plant = _plant()
    decision = watering_decision(
        plant,
        _states(moisture=10),
        last_watered_at=None,
        requested_seconds=999,
        now=NOW,
    )

    assert decision.allowed
    assert decision.seconds == plant.watering.max_seconds


def _plant() -> PlantConfig:
    return PlantConfig(
        id="office_shelf_golden_pothos",
        name="Golden Pothos",
        location="Office shelf",
        species="golden_pothos",
        plant_entity="plant.office_shelf_golden_pothos",
        entities=EntityMap(
            moisture="sensor.moisture",
            pump="switch.office_watering_kit",
        ),
    )


def _states(moisture: float, age_hours: int = 1) -> dict[str, EntityState]:
    updated = NOW - timedelta(hours=age_hours)
    return {
        "sensor.moisture": EntityState(
            entity_id="sensor.moisture",
            state=str(moisture),
            attributes={},
            last_changed=updated,
            last_updated=updated,
        )
    }

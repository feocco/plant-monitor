from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plant_monitor.monitor import (
    SensorReading,
    _alert_key,
    _next_alert_summary,
    _status_counts,
    _watering_lookback_message,
)
from plant_monitor.models import EntityMap, Issue, PlantConfig, PlantStatus, Severity
from plant_monitor.runtime_state import RuntimeState

NOW = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)


def test_alert_key_ignores_numeric_value_drift_for_same_issue() -> None:
    first = PlantStatus(
        plant_id="pothos",
        label=Severity.RED,
        issues=(Issue(Severity.RED, "moisture", "moisture is low at 14.5%."),),
        watering_recommended=False,
        summary="",
    )
    second = PlantStatus(
        plant_id="pothos",
        label=Severity.RED,
        issues=(Issue(Severity.RED, "moisture", "moisture is low at 13.9%."),),
        watering_recommended=False,
        summary="",
    )

    assert _alert_key(first) == _alert_key(second)


def test_status_counts_groups_green_orange_red() -> None:
    statuses = [
        PlantStatus("green", Severity.GREEN, (), False, "green"),
        PlantStatus("orange", Severity.ORANGE, (), False, "orange"),
        PlantStatus("red", Severity.RED, (), False, "red"),
        PlantStatus("red-water", Severity.RED, (), True, "red"),
    ]

    counts = _status_counts(statuses)

    assert counts[Severity.GREEN] == 1
    assert counts[Severity.ORANGE] == 1
    assert counts[Severity.RED] == 2


def test_next_alert_summary_reports_earliest_repeat() -> None:
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    plant = _plant()
    status = PlantStatus(
        plant.id,
        Severity.RED,
        (Issue(Severity.RED, "moisture", "moisture is low."),),
        False,
        "red",
    )
    state = RuntimeState(last_alert_sent_at={plant.id: now - timedelta(hours=22, minutes=30)})

    assert _next_alert_summary([plant], [status], state, repeat_hours=24, now=now) == "in 1h 30m"


def test_next_alert_summary_reports_none_when_all_green() -> None:
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    plant = _plant()
    status = PlantStatus(plant.id, Severity.GREEN, (), False, "green")

    assert _next_alert_summary([plant], [status], RuntimeState(), repeat_hours=24, now=now) == "none"


def test_watering_lookback_message_reports_sensor_movement() -> None:
    before = [
        SensorReading("moisture", "sensor.moisture", 12.0, NOW),
        SensorReading("humidity", "sensor.humidity", 41.0, NOW),
    ]
    after = [
        SensorReading("moisture", "sensor.moisture", 15.5, NOW + timedelta(hours=1)),
        SensorReading("humidity", "sensor.humidity", 41.2, NOW + timedelta(hours=1)),
    ]

    message = _watering_lookback_message(before, after, timedelta(hours=1))

    assert "moisture: 12.0 -> 15.5 (+3.5)" in message
    assert "Result: measurable sensor movement detected." in message


def _plant() -> PlantConfig:
    return PlantConfig(
        id="office_shelf_golden_pothos",
        name="Golden Pothos",
        location="Office shelf",
        species="golden_pothos",
        plant_entity="plant.office_shelf_golden_pothos",
        entities=EntityMap(),
    )

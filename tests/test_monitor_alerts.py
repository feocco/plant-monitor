from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plant_monitor.monitor import (
    PlantMonitor,
    SensorReading,
    _alert_key,
    _next_alert_summary,
    _status_counts,
    _watering_lookback_message,
)
from plant_monitor.models import EntityMap, EntityState, Issue, PlantConfig, PlantStatus, ServiceConfig, Severity
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


async def test_many_evaluations_send_one_condition_notification(tmp_path) -> None:
    plant = _plant(species="ficus_altissima")
    monitor = PlantMonitor(
        _config(tmp_path / "state.json"),
        [plant],
        _FakeHA(),
        RuntimeState(),
    )
    notifier = _FakeNotifier()
    monitor.notifier = notifier
    monitor.states = _states(moisture=20, updated=NOW)

    await monitor.evaluate_and_notify(now=NOW)
    monitor.states = _states(moisture=19, updated=NOW + timedelta(hours=24))
    await monitor.evaluate_and_notify(now=NOW + timedelta(hours=24))
    monitor.states = _states(moisture=18, updated=NOW + timedelta(hours=25))
    await monitor.evaluate_and_notify(now=NOW + timedelta(hours=25))

    assert notifier.urgent == [("office_shelf_golden_pothos", Severity.RED)]


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
        ),
    )


def _states(moisture: float, updated: datetime) -> dict[str, EntityState]:
    return {
        "sensor.moisture": _state("sensor.moisture", moisture, updated),
        "sensor.temperature": _state("sensor.temperature", 72, updated),
        "sensor.humidity": _state("sensor.humidity", 55, updated),
        "sensor.battery": _state("sensor.battery", 88, updated),
    }


def _state(entity_id: str, value: float, updated: datetime) -> EntityState:
    return EntityState(
        entity_id=entity_id,
        state=str(value),
        attributes={},
        last_changed=updated,
        last_updated=updated,
    )


class _FakeHA:
    def add_event_handler(self, handler) -> None:
        self.handler = handler


class _FakeNotifier:
    def __init__(self) -> None:
        self.urgent: list[tuple[str, Severity]] = []

    async def send_urgent(self, plant: PlantConfig, status: PlantStatus, message=None) -> None:
        self.urgent.append((plant.id, status.label))


def _config(state_path) -> ServiceConfig:
    return ServiceConfig(
        ha_url="http://homeassistant.local",
        ha_token="token",
        homelab_functions_url=None,
        homelab_functions_token=None,
        plants_dashboard_url="/lovelace/plants",
        alert_snooze_hours=24,
        alert_repeat_hours=24,
        config_path="plants.yaml",
        state_path=str(state_path),
        service_host="127.0.0.1",
        service_port=0,
        callback_token="",
        dry_run=False,
        log_level="INFO",
        timezone="UTC",
    )

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from plant_monitor.models import EntityMap, EntityState, PlantConfig, ServiceConfig
from plant_monitor.monitor import PlantMonitor, SensorReading, _watering_lookback_job
from plant_monitor.runtime_state import RuntimeState

NOW = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)


async def test_due_watering_lookback_sends_message_and_removes_job(tmp_path: Path) -> None:
    plant = _plant()
    state_path = tmp_path / "state.json"
    job = _watering_lookback_job(
        plant,
        NOW - timedelta(hours=1, minutes=5),
        timedelta(hours=1),
        [
            SensorReading(
                "moisture",
                "sensor.office_pothos_moisture",
                12.0,
                NOW - timedelta(hours=1),
            )
        ],
    )
    state = RuntimeState(scheduled_jobs=[job])
    monitor = PlantMonitor(
        _config(state_path),
        [plant],
        _FakeHA(
            {"sensor.office_pothos_moisture": _state("sensor.office_pothos_moisture", 15.5)}
        ),
        state,
    )
    notifier = _FakeNotifier()
    monitor.notifier = notifier

    await monitor._run_due_scheduled_jobs(now=NOW)

    assert state.scheduled_jobs == []
    assert RuntimeState.load(state_path).scheduled_jobs == []
    assert notifier.lookbacks == [
        (
            plant.id,
            "Watering research after 1h:\n"
            "- moisture: 12.0 -> 15.5 (+3.5)\n"
            "Result: measurable sensor movement detected.",
        )
    ]


async def test_due_watering_lookback_stays_queued_when_notification_fails(tmp_path: Path) -> None:
    plant = _plant()
    state_path = tmp_path / "state.json"
    job = _watering_lookback_job(
        plant,
        NOW - timedelta(hours=1, minutes=5),
        timedelta(hours=1),
        [
            SensorReading(
                "moisture",
                "sensor.office_pothos_moisture",
                12.0,
                NOW - timedelta(hours=1),
            )
        ],
    )
    state = RuntimeState(scheduled_jobs=[job])
    state.save(state_path)
    monitor = PlantMonitor(
        _config(state_path),
        [plant],
        _FakeHA(
            {"sensor.office_pothos_moisture": _state("sensor.office_pothos_moisture", 15.5)}
        ),
        state,
    )
    monitor.notifier = _FakeNotifier(fail=True)

    await monitor._run_due_scheduled_jobs(now=NOW)

    assert state.scheduled_jobs == [job]
    assert RuntimeState.load(state_path).scheduled_jobs == [job]


class _FakeHA:
    def __init__(self, states: dict[str, EntityState]) -> None:
        self.states = states
        self.event_handlers = []

    def add_event_handler(self, handler) -> None:
        self.event_handlers.append(handler)

    async def get_states(self) -> dict[str, EntityState]:
        return self.states


class _FakeNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.lookbacks: list[tuple[str, str]] = []

    async def send_watering_lookback(self, plant: PlantConfig, message: str) -> None:
        if self.fail:
            raise RuntimeError("notification failed")
        self.lookbacks.append((plant.id, message))


def _plant() -> PlantConfig:
    return PlantConfig(
        id="office_pothos",
        name="Office Pothos",
        location="Office",
        species="golden_pothos",
        plant_entity=None,
        entities=EntityMap(moisture="sensor.office_pothos_moisture"),
    )


def _state(entity_id: str, value: float) -> EntityState:
    return EntityState(
        entity_id=entity_id,
        state=str(value),
        attributes={},
        last_changed=NOW,
        last_updated=NOW,
    )


def _config(state_path: Path) -> ServiceConfig:
    return ServiceConfig(
        ha_url="http://homeassistant.local",
        ha_token="token",
        homelab_functions_url="http://homelab-functions:8091",
        homelab_functions_token="token",
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

from __future__ import annotations

from pathlib import Path

from plant_monitor.models import EntityState, ServiceConfig
from plant_monitor.monitor import PlantMonitor
from plant_monitor.runtime_state import RuntimeState


async def test_run_connected_returns_when_ha_connection_closes(tmp_path: Path) -> None:
    ha = _FakeHA()
    monitor = PlantMonitor(_config(tmp_path / "state.json"), [], ha, RuntimeState())
    callback_server = _FakeCallbackServer()
    monitor.callback_server = callback_server

    await monitor._run_connected()

    assert ha.connected
    assert ha.subscriptions == ["state_changed", "mobile_app_notification_action"]
    assert ha.closed
    assert callback_server.started
    assert callback_server.stopped


async def test_run_connected_cleans_up_when_startup_evaluation_fails(tmp_path: Path) -> None:
    ha = _FakeHA()
    monitor = PlantMonitor(_config(tmp_path / "state.json"), [], ha, RuntimeState())
    callback_server = _FakeCallbackServer()
    monitor.callback_server = callback_server

    async def raise_after_callback_start(now=None):
        raise RuntimeError("notification failed")

    monitor.evaluate_and_notify = raise_after_callback_start

    try:
        await monitor._run_connected()
    except RuntimeError as exc:
        assert str(exc) == "notification failed"
    else:
        raise AssertionError("expected startup failure")

    assert callback_server.started
    assert callback_server.stopped
    assert ha.closed


class _FakeHA:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.subscriptions: list[str] = []
        self.event_handlers = []

    def add_event_handler(self, handler) -> None:
        self.event_handlers.append(handler)

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    async def get_states(self) -> dict[str, EntityState]:
        return {}

    async def subscribe_events(self, event_type: str | None = None) -> None:
        if event_type is not None:
            self.subscriptions.append(event_type)


class _FakeCallbackServer:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def _config(state_path: Path) -> ServiceConfig:
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

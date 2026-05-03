from __future__ import annotations

import asyncio

import pytest

from plant_monitor.ha import HomeAssistantClient, _websocket_url, parse_entity_state
from plant_monitor.models import ServiceConfig


def test_websocket_url_from_http_url() -> None:
    assert _websocket_url("http://homeassistant.local:8123") == "ws://homeassistant.local:8123/api/websocket"


def test_websocket_url_from_https_url() -> None:
    assert _websocket_url("https://ha.example.com") == "wss://ha.example.com/api/websocket"


def test_parse_entity_state_handles_zulu_timestamps() -> None:
    state = parse_entity_state(
        {
            "entity_id": "sensor.moisture",
            "state": "41",
            "attributes": {"friendly_name": "Moisture"},
            "last_changed": "2026-05-02T12:00:00Z",
            "last_updated": "2026-05-02T12:05:00Z",
        }
    )

    assert state.entity_id == "sensor.moisture"
    assert state.last_updated.isoformat() == "2026-05-02T12:05:00+00:00"


async def test_finish_pending_ignores_cancelled_future() -> None:
    client = HomeAssistantClient(_config())
    future = asyncio.get_running_loop().create_future()
    client._pending[1] = future
    future.cancel()

    client._finish_pending({"id": 1, "success": True, "result": {}})

    assert client._pending == {}


async def test_request_timeout_removes_pending_future(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HomeAssistantClient(_config())
    client._ws = _FakeWebSocket()

    async def timeout_wait_for(future, timeout):
        future.cancel()
        raise TimeoutError

    monkeypatch.setattr("plant_monitor.ha.asyncio.wait_for", timeout_wait_for)

    with pytest.raises(TimeoutError):
        await client.request({"type": "get_states"})

    assert client._pending == {}
    client._finish_pending({"id": 1, "success": True, "result": {}})


async def test_close_swallows_finished_reader_task_error() -> None:
    client = HomeAssistantClient(_config())

    async def fail_reader() -> None:
        raise asyncio.InvalidStateError("invalid state")

    client._reader_task = asyncio.create_task(fail_reader())
    await asyncio.sleep(0)

    await client.close()

    assert client._reader_task is None


class _FakeWebSocket:
    closed = False

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def _config() -> ServiceConfig:
    return ServiceConfig(
        ha_url="http://homeassistant.local:8123",
        ha_token="token",
        homelab_functions_url=None,
        homelab_functions_token=None,
        plants_dashboard_url="/lovelace/plants",
        alert_snooze_hours=24,
        alert_repeat_hours=24,
        config_path="plants.yaml",
        state_path="data/state.json",
        service_host="127.0.0.1",
        service_port=0,
        callback_token="",
        dry_run=False,
        log_level="INFO",
        timezone="UTC",
    )

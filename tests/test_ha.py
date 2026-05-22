from __future__ import annotations

import pytest
from aiohttp.client_exceptions import ClientConnectionResetError

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


async def test_get_states_parses_shared_client_states() -> None:
    client = HomeAssistantClient(_config())
    client._client = _FakeSharedClient(
        states=[
            {
                "entity_id": "sensor.moisture",
                "state": "41",
                "attributes": {"friendly_name": "Moisture"},
                "last_changed": "2026-05-02T12:00:00Z",
                "last_updated": "2026-05-02T12:05:00Z",
            }
        ]
    )

    states = await client.get_states()

    assert states["sensor.moisture"].state == "41"
    assert states["sensor.moisture"].last_updated.isoformat() == "2026-05-02T12:05:00+00:00"


async def test_call_service_delegates_to_shared_client() -> None:
    client = HomeAssistantClient(_config())
    shared_client = _FakeSharedClient()
    client._client = shared_client

    result = await client.call_service("switch", "turn_on", {"entity_id": "switch.pump"})

    assert result == {"ok": True}
    assert shared_client.service_calls == [
        ("switch", "turn_on", {"entity_id": "switch.pump"}),
    ]


async def test_dry_run_call_service_skips_shared_client() -> None:
    client = HomeAssistantClient(_config(dry_run=True))
    shared_client = _FakeSharedClient()
    client._client = shared_client

    result = await client.call_service("switch", "turn_on", {"entity_id": "switch.pump"})

    assert result == {"dry_run": True}
    assert shared_client.service_calls == []


async def test_wait_closed_delegates_to_shared_client() -> None:
    client = HomeAssistantClient(_config())
    shared_client = _FakeSharedClient()
    client._client = shared_client

    await client.wait_closed()

    assert shared_client.wait_closed_called


async def test_close_ignores_cleanup_reset_and_replaces_shared_client() -> None:
    client = HomeAssistantClient(_config())
    broken_client = _FakeSharedClient(close_error=ClientConnectionResetError("closed"))
    replacement_client = _FakeSharedClient()
    client._client = broken_client
    client._new_client = lambda: replacement_client

    await client.close()

    assert broken_client.close_called
    assert client._client is replacement_client


async def test_connect_failure_closes_and_replaces_shared_client() -> None:
    client = HomeAssistantClient(_config())
    broken_client = _FakeSharedClient(connect_error=RuntimeError("connect failed"))
    replacement_client = _FakeSharedClient()
    client._client = broken_client
    client._new_client = lambda: replacement_client

    with pytest.raises(RuntimeError, match="connect failed"):
        await client.connect()

    assert broken_client.close_called
    assert client._client is replacement_client


async def test_event_dispatch_continues_after_handler_failure() -> None:
    client = HomeAssistantClient(_config())
    handled: list[dict] = []

    async def failing_handler(event: dict) -> None:
        raise RuntimeError("boom")

    async def recording_handler(event: dict) -> None:
        handled.append(event)

    client.add_event_handler(failing_handler)
    client.add_event_handler(recording_handler)

    await client._dispatch_event({"event_type": "state_changed"})

    assert handled == [{"event_type": "state_changed"}]


class _FakeSharedClient:
    def __init__(
        self,
        states: list[dict] | None = None,
        connect_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.states = states or []
        self.connect_error = connect_error
        self.close_error = close_error
        self.close_called = False
        self.service_calls: list[tuple[str, str, dict]] = []
        self.wait_closed_called = False

    async def connect(self) -> None:
        if self.connect_error:
            raise self.connect_error

    async def close(self) -> None:
        self.close_called = True
        if self.close_error:
            raise self.close_error

    async def get_states(self) -> list[dict]:
        return self.states

    async def call_service(self, domain: str, service: str, service_data: dict) -> dict:
        self.service_calls.append((domain, service, service_data))
        return {"ok": True}

    async def wait_closed(self) -> None:
        self.wait_closed_called = True


def _config(dry_run: bool = False) -> ServiceConfig:
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
        dry_run=dry_run,
        log_level="INFO",
        timezone="UTC",
    )

from __future__ import annotations

from plant_monitor.ha import _websocket_url, parse_entity_state


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


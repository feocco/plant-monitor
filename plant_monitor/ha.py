from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from homelab import HomeAssistantConfig, HomeAssistantWebSocketClient, websocket_url

from plant_monitor.models import EntityState, ServiceConfig

LOGGER = logging.getLogger(__name__)
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class HomeAssistantClient:
    """Plant-monitor adapter around the shared homelab Home Assistant client.

    The shared client owns the WebSocket connection, auth flow, request ids, and
    event stream. This adapter keeps plant-monitor-specific behavior local:
    parsed entity state objects, dry-run service calls, and guarded event
    handler dispatch.
    """

    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self._client = HomeAssistantWebSocketClient(
            HomeAssistantConfig(
                ha_url=config.ha_url,
                ha_long_lived_token=config.ha_token,
            )
        )
        self._event_handlers: list[EventHandler] = []
        self._client.add_event_handler(self._dispatch_event)

    async def connect(self) -> None:
        try:
            await self._client.connect()
            LOGGER.info("Connected to Home Assistant WebSocket")
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        await self._client.close()

    async def wait_closed(self) -> None:
        await self._client.wait_closed()

    def add_event_handler(self, handler: EventHandler) -> None:
        self._event_handlers.append(handler)

    async def get_states(self) -> dict[str, EntityState]:
        states = await self._client.get_states()
        return {state["entity_id"]: parse_entity_state(state) for state in states}

    async def subscribe_events(self, event_type: str | None = None) -> None:
        await self._client.subscribe_events(event_type)

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.config.dry_run:
            LOGGER.info(
                "DRY_RUN call_service %s.%s %s",
                domain,
                service,
                service_data or {},
            )
            return {"dry_run": True}
        return await self._client.call_service(domain, service, service_data or {})

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._client.request(payload)

    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        for handler in list(self._event_handlers):
            try:
                await handler(event)
            except Exception:
                LOGGER.exception("Event handler failed")


def parse_entity_state(raw: dict[str, Any]) -> EntityState:
    return EntityState(
        entity_id=raw["entity_id"],
        state=str(raw.get("state", "")),
        attributes=raw.get("attributes") or {},
        last_changed=_parse_datetime(raw.get("last_changed")),
        last_updated=_parse_datetime(raw.get("last_updated")),
    )


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now().astimezone()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _websocket_url(ha_url: str) -> str:
    return websocket_url(ha_url)

from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, urlunparse

import aiohttp

from plant_monitor.models import EntityState, ServiceConfig

LOGGER = logging.getLogger(__name__)
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class HomeAssistantClient:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._event_handlers: list[EventHandler] = []
        self._reader_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        await self.close()
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(_websocket_url(self.config.ha_url))
        auth_required = await self._ws.receive_json()
        if auth_required.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected Home Assistant auth handshake: {auth_required}")
        await self._ws.send_json({"type": "auth", "access_token": self.config.ha_token})
        auth_response = await self._ws.receive_json()
        if auth_response.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant authentication failed: {auth_response}")
        self._reader_task = asyncio.create_task(self._reader(), name="ha-websocket-reader")
        LOGGER.info("Connected to Home Assistant WebSocket")

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
        self._ws = None
        self._session = None

    def add_event_handler(self, handler: EventHandler) -> None:
        self._event_handlers.append(handler)

    async def get_states(self) -> dict[str, EntityState]:
        result = await self.request({"type": "get_states"})
        states = result.get("result") or []
        return {state["entity_id"]: parse_entity_state(state) for state in states}

    async def subscribe_events(self, event_type: str | None = None) -> None:
        payload: dict[str, Any] = {"type": "subscribe_events"}
        if event_type:
            payload["event_type"] = event_type
        await self.request(payload)

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.config.dry_run:
            LOGGER.info("DRY_RUN call_service %s.%s %s", domain, service, service_data or {})
            return {"dry_run": True}
        return await self.request(
            {
                "type": "call_service",
                "domain": domain,
                "service": service,
                "service_data": service_data or {},
            }
        )

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._ws or self._ws.closed:
            raise RuntimeError("Home Assistant WebSocket is not connected")
        message_id = next(self._ids)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[message_id] = future
        await self._ws.send_json({"id": message_id, **payload})
        return await asyncio.wait_for(future, timeout=30)

    async def _reader(self) -> None:
        if self._ws is None:
            raise RuntimeError("Home Assistant WebSocket reader started without a connection")
        async for message in self._ws:
            if message.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError(f"Home Assistant WebSocket error: {self._ws.exception()}")
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            payload = message.json()
            if payload.get("type") == "result":
                self._finish_pending(payload)
            elif payload.get("type") == "event":
                await self._dispatch_event(payload.get("event") or {})
            else:
                LOGGER.debug("Ignoring Home Assistant message: %s", payload)

    def _finish_pending(self, payload: dict[str, Any]) -> None:
        message_id = payload.get("id")
        future = self._pending.pop(message_id, None)
        if not future:
            return
        if payload.get("success", False):
            future.set_result(payload)
        else:
            future.set_exception(RuntimeError(f"Home Assistant request failed: {payload}"))

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
    parsed = urlparse(ha_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=scheme, path="/api/websocket", params="", query="", fragment=""))

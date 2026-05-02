from __future__ import annotations

import logging
from typing import Awaitable, Callable

from aiohttp import web

LOGGER = logging.getLogger(__name__)
WaterCallback = Callable[[str, int | None], Awaitable[tuple[int, dict]]]


class CallbackServer:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        water_callback: WaterCallback,
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.water_callback = water_callback
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.add_routes(
            [
                web.get("/health", self._health),
                web.post("/water/{plant_id}", self._water),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        LOGGER.info("Callback server listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _water(self, request: web.Request) -> web.Response:
        if self.token:
            supplied = request.headers.get("X-Plant-Monitor-Token") or request.query.get("token")
            if supplied != self.token:
                return web.json_response({"error": "unauthorized"}, status=401)
        body = await _json_or_empty(request)
        seconds = _coerce_seconds(body.get("seconds"))
        status, payload = await self.water_callback(request.match_info["plant_id"], seconds)
        return web.json_response(payload, status=status)


async def _json_or_empty(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        value = await request.json()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _coerce_seconds(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

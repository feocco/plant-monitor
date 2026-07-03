from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

from aiohttp import web

LOGGER = logging.getLogger(__name__)
WaterCallback = Callable[[str, int | None], Awaitable[tuple[int, dict]]]
CALLBACK_TOKEN_KEY: web.AppKey[str] = web.AppKey("callback_token")
WATER_CALLBACK_KEY: web.AppKey[WaterCallback] = web.AppKey("water_callback")


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
        app = create_app(token=self.token, water_callback=self.water_callback)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        LOGGER.info("Callback server listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None


def create_app(token: str, water_callback: WaterCallback) -> web.Application:
    app = web.Application()
    app[CALLBACK_TOKEN_KEY] = token
    app[WATER_CALLBACK_KEY] = water_callback
    app.add_routes(
        [
            web.get("/health", _health),
            web.get("/docs", _docs),
            web.get("/openapi.json", _openapi_json, name="openapi"),
            web.post("/water/{plant_id}", _water),
        ]
    )
    return app


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _docs(request: web.Request) -> web.Response:
    token_configured = bool(request.app[CALLBACK_TOKEN_KEY])
    callback_note = (
        "When SERVICE_CALLBACK_TOKEN is configured, callers must send either the "
        "X-Plant-Monitor-Token header or the token query parameter."
        if token_configured
        else "When SERVICE_CALLBACK_TOKEN is empty, the callback endpoint does not require a token."
    )
    openapi_url = request.app.router["openapi"].url_for()
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Plant Monitor Service Docs</title>
    <style>
      :root {{
        color-scheme: light dark;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      body {{
        margin: 0;
        background: #f6f7fb;
        color: #172033;
      }}
      main {{
        max-width: 900px;
        margin: 0 auto;
        padding: 32px 20px 48px;
      }}
      h1, h2 {{
        margin-bottom: 0.4rem;
      }}
      p, li {{
        line-height: 1.5;
      }}
      code, pre {{
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      }}
      .endpoint {{
        background: #ffffff;
        border: 1px solid #d8deeb;
        border-radius: 8px;
        padding: 16px;
        margin: 16px 0;
      }}
      .method {{
        display: inline-block;
        min-width: 58px;
        padding: 4px 8px;
        border-radius: 6px;
        font-weight: 600;
        color: #ffffff;
        background: #0b6bcb;
      }}
      .path {{
        margin-left: 10px;
        font-weight: 600;
      }}
      pre {{
        overflow-x: auto;
        background: #101827;
        color: #f8fafc;
        border-radius: 8px;
        padding: 12px;
      }}
      a {{
        color: #0b6bcb;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Plant Monitor Service Docs</h1>
      <p>Small operational docs for the callback and health surface exposed by Plant Monitor.</p>

      <section class="endpoint">
        <h2>GET /health</h2>
        <p>Readiness endpoint used by local runtime checks and homelab health probes.</p>
        <pre>{{"ok": true}}</pre>
      </section>

      <section class="endpoint">
        <h2>POST /water/{{plant_id}}</h2>
        <p>Manual watering callback for a configured plant id. Request body may include an optional <code>seconds</code> integer.</p>
        <p>{callback_note}</p>
        <ul>
          <li><code>X-Plant-Monitor-Token: &lt;token&gt;</code></li>
          <li><code>POST /water/fern?token=&lt;token&gt;</code></li>
        </ul>
        <pre>{json.dumps({"seconds": 7}, indent=2)}</pre>
      </section>

      <section>
        <p>Machine-readable contract: <a href="{openapi_url}">{openapi_url}</a></p>
      </section>
    </main>
  </body>
</html>"""
    return web.Response(text=html, content_type="text/html")


async def _openapi_json(request: web.Request) -> web.Response:
    return web.json_response(_openapi_document())


async def _water(request: web.Request) -> web.Response:
    token = request.app[CALLBACK_TOKEN_KEY]
    if token:
        supplied = request.headers.get("X-Plant-Monitor-Token") or request.query.get("token")
        if supplied != token:
            return web.json_response({"error": "unauthorized"}, status=401)
    body = await _json_or_empty(request)
    seconds = _coerce_seconds(body.get("seconds"))
    water_callback = request.app[WATER_CALLBACK_KEY]
    status, payload = await water_callback(request.match_info["plant_id"], seconds)
    return web.json_response(payload, status=status)


def _openapi_document() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Plant Monitor",
            "version": "0.1.0",
            "description": (
                "Operational API surface for Plant Monitor health checks and manual watering callbacks."
            ),
        },
        "paths": {
            "/health": {
                "get": {
                    "summary": "Health check",
                    "responses": {
                        "200": {
                            "description": "Service is running.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"ok": {"type": "boolean"}},
                                        "required": ["ok"],
                                        "additionalProperties": False,
                                    },
                                    "example": {"ok": True},
                                }
                            },
                        }
                    },
                }
            },
            "/water/{plant_id}": {
                "post": {
                    "summary": "Run a guarded watering callback",
                    "description": (
                        "When SERVICE_CALLBACK_TOKEN is configured, callers must send either "
                        "the X-Plant-Monitor-Token header or the token query parameter. "
                        "Without that config, the endpoint remains unauthenticated."
                    ),
                    "parameters": [
                        {
                            "name": "plant_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "token",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": (
                                "Alternative callback token. Required when SERVICE_CALLBACK_TOKEN is configured "
                                "and the header is not used."
                            ),
                        },
                    ],
                    "security": [
                        {"PlantMonitorTokenHeader": []},
                        {"PlantMonitorTokenQuery": []},
                    ],
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "seconds": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "description": "Optional requested watering duration in seconds.",
                                        }
                                    },
                                    "additionalProperties": True,
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Watering request accepted or completed."
                        },
                        "401": {
                            "description": "Missing or invalid callback token."
                        },
                        "404": {
                            "description": "Unknown plant id."
                        },
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "PlantMonitorTokenHeader": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Plant-Monitor-Token",
                },
                "PlantMonitorTokenQuery": {
                    "type": "apiKey",
                    "in": "query",
                    "name": "token",
                },
            }
        },
    }


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

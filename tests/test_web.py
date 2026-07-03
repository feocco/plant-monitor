from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer

from plant_monitor.web import create_app


async def test_health_response_is_unchanged() -> None:
    app = create_app(token="", water_callback=_water_callback)
    async with TestServer(app) as server, TestClient(server) as client:
        response = await client.get("/health")

        assert response.status == 200
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        assert await response.json() == {"ok": True}


async def test_docs_returns_browser_friendly_html() -> None:
    app = create_app(token="", water_callback=_water_callback)
    async with TestServer(app) as server, TestClient(server) as client:
        response = await client.get("/docs")

        assert response.status == 200
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        text = await response.text()
        assert "<title>Plant Monitor Service Docs</title>" in text
        assert "GET /health" in text
        assert "POST /water/{plant_id}" in text
        assert "X-Plant-Monitor-Token" in text
        assert "?token=" in text


async def test_openapi_returns_valid_service_contract() -> None:
    app = create_app(token="secret", water_callback=_water_callback)
    async with TestServer(app) as server, TestClient(server) as client:
        response = await client.get("/openapi.json")

        assert response.status == 200
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        payload = await response.json()
        assert payload["openapi"] == "3.1.0"
        assert payload["info"]["title"] == "Plant Monitor"
        assert payload["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"]
        water = payload["paths"]["/water/{plant_id}"]["post"]
        assert water["security"] == [
            {"PlantMonitorTokenHeader": []},
            {"PlantMonitorTokenQuery": []},
        ]
        assert "SERVICE_CALLBACK_TOKEN" in water["description"]


async def test_callback_requires_token_when_configured() -> None:
    app = create_app(token="secret", water_callback=_water_callback)
    async with TestServer(app) as server, TestClient(server) as client:
        response = await client.post("/water/fern")

        assert response.status == 401
        assert await response.json() == {"error": "unauthorized"}


async def test_callback_accepts_header_token_and_seconds_body() -> None:
    app = create_app(token="secret", water_callback=_water_callback)
    async with TestServer(app) as server, TestClient(server) as client:
        response = await client.post(
            "/water/fern",
            headers={"X-Plant-Monitor-Token": "secret"},
            json={"seconds": 7},
        )

        assert response.status == 202
        assert await response.json() == {"plant_id": "fern", "seconds": 7}


async def test_callback_accepts_query_token() -> None:
    app = create_app(token="secret", water_callback=_water_callback)
    async with TestServer(app) as server, TestClient(server) as client:
        response = await client.post("/water/fern?token=secret")

        assert response.status == 202
        assert await response.json() == {"plant_id": "fern", "seconds": None}


async def _water_callback(plant_id: str, seconds: int | None) -> tuple[int, dict]:
    return 202, {"plant_id": plant_id, "seconds": seconds}

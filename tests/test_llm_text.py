from __future__ import annotations

from plant_monitor.llm_text import rewrite_notification_text
from plant_monitor.models import EntityMap, Issue, PlantConfig, PlantStatus, ServiceConfig, Severity


async def test_llm_text_disabled_by_default_returns_fallback() -> None:
    text = await rewrite_notification_text(
        _config(llm=False, api_key=None),
        _plant(),
        _status(),
        "fallback text",
    )

    assert text == "fallback text"


async def test_llm_text_failure_returns_deterministic_fallback(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr("plant_monitor.llm_text._rewrite_sync", fail)

    text = await rewrite_notification_text(
        _config(llm=True, api_key="sk-test"),
        _plant(),
        _status(),
        "fallback text",
    )

    assert text == "fallback text"


def _plant() -> PlantConfig:
    return PlantConfig(
        id="pothos",
        name="Golden Pothos",
        location="Office",
        species="golden_pothos",
        plant_entity=None,
        entities=EntityMap(),
    )


def _status() -> PlantStatus:
    return PlantStatus(
        plant_id="pothos",
        label=Severity.RED,
        issues=(Issue(Severity.RED, "moisture", "moisture has stayed low at 12%."),),
        watering_recommended=False,
        summary="",
    )


def _config(llm: bool, api_key: str | None) -> ServiceConfig:
    return ServiceConfig(
        ha_url="http://homeassistant.local",
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
        openai_api_key=api_key,
        llm_notification_text=llm,
    )

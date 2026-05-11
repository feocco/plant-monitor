from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any


class Severity(IntEnum):
    GREEN = 0
    ORANGE = 1
    RED = 2

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class ThresholdRange:
    min_green: float | None = None
    min_orange: float | None = None
    max_green: float | None = None
    max_orange: float | None = None


@dataclass(frozen=True)
class SpeciesThresholds:
    moisture: ThresholdRange
    temperature: ThresholdRange
    humidity: ThresholdRange
    conductivity: ThresholdRange = field(default_factory=ThresholdRange)
    brightness: ThresholdRange = field(default_factory=ThresholdRange)
    battery_orange: float = 30.0
    battery_red: float = 15.0


@dataclass(frozen=True)
class EntityMap:
    moisture: str | None = None
    temperature: str | None = None
    humidity: str | None = None
    battery: str | None = None
    conductivity: str | None = None
    brightness: str | None = None
    pump: str | None = None


@dataclass(frozen=True)
class WateringConfig:
    switch: str | None = None
    max_seconds: int = 8
    cooldown_hours: int = 48


@dataclass(frozen=True)
class PlantConfig:
    id: str
    name: str
    location: str
    species: str
    plant_entity: str | None
    entities: EntityMap
    thresholds: SpeciesThresholds | None = None
    watering: WateringConfig = field(default_factory=WateringConfig)


@dataclass(frozen=True)
class ServiceConfig:
    ha_url: str
    ha_token: str
    homelab_functions_url: str | None
    homelab_functions_token: str | None
    plants_dashboard_url: str
    alert_snooze_hours: int
    alert_repeat_hours: int
    config_path: str
    state_path: str
    service_host: str
    service_port: int
    callback_token: str
    dry_run: bool
    log_level: str
    timezone: str
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    llm_notification_text: bool = False


@dataclass
class EntityState:
    entity_id: str
    state: str
    attributes: dict[str, Any]
    last_changed: datetime
    last_updated: datetime


@dataclass(frozen=True)
class Issue:
    severity: Severity
    sensor: str
    message: str


@dataclass(frozen=True)
class PlantStatus:
    plant_id: str
    label: Severity
    issues: tuple[Issue, ...]
    watering_recommended: bool
    summary: str


@dataclass(frozen=True)
class WateringDecision:
    allowed: bool
    plant_id: str
    seconds: int
    reasons: tuple[str, ...]

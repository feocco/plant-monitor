from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plant_monitor.models import EntityState, PlantConfig, Severity, SpeciesThresholds, ThresholdRange
from plant_monitor.thresholds import DEFAULT_THRESHOLDS, SPECIES_THRESHOLDS

STALE_ORANGE_HOURS = 12
STALE_RED_HOURS = 24
BATTERY_STALE_ORANGE_HOURS = 24 * 5
BATTERY_STALE_RED_HOURS = 24 * 10


def thresholds_for(plant: PlantConfig) -> SpeciesThresholds:
    return plant.thresholds or SPECIES_THRESHOLDS.get(plant.species, DEFAULT_THRESHOLDS)


def numeric_state(state: EntityState | None) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def staleness(state: EntityState, now: datetime) -> Severity:
    age = now - aware(state.last_updated)
    if age >= timedelta(hours=STALE_RED_HOURS):
        return Severity.RED
    if age >= timedelta(hours=STALE_ORANGE_HOURS):
        return Severity.ORANGE
    return Severity.GREEN


def staleness_for_sensor(sensor: str, state: EntityState, now: datetime) -> Severity:
    if sensor == "battery":
        age = now - aware(state.last_updated)
        if age >= timedelta(hours=BATTERY_STALE_RED_HOURS):
            return Severity.RED
        if age >= timedelta(hours=BATTERY_STALE_ORANGE_HOURS):
            return Severity.ORANGE
        return Severity.GREEN
    return staleness(state, now)


def severity_for_low(value: float, threshold: ThresholdRange) -> Severity:
    if threshold.min_orange is not None and value < threshold.min_orange:
        return Severity.RED
    if threshold.min_green is not None and value < threshold.min_green:
        return Severity.ORANGE
    return Severity.GREEN


def severity_for_high(value: float, threshold: ThresholdRange) -> Severity:
    if threshold.max_orange is not None and value > threshold.max_orange:
        return Severity.RED
    if threshold.max_green is not None and value > threshold.max_green:
        return Severity.ORANGE
    return Severity.GREEN


def aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

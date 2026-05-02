from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plant_monitor.models import (
    EntityState,
    Issue,
    PlantConfig,
    PlantStatus,
    Severity,
    SpeciesThresholds,
    ThresholdRange,
    WateringDecision,
)
from plant_monitor.thresholds import DEFAULT_THRESHOLDS, SPECIES_THRESHOLDS

STALE_ORANGE_HOURS = 12
STALE_RED_HOURS = 24
BATTERY_STALE_ORANGE_HOURS = 24 * 5
BATTERY_STALE_RED_HOURS = 24 * 10


def evaluate_plant(
    plant: PlantConfig,
    states: dict[str, EntityState],
    now: datetime | None = None,
) -> PlantStatus:
    now = _aware(now)
    thresholds = _thresholds_for(plant)
    issues: list[Issue] = []

    _add_plant_entity_issue(issues, plant, states)
    _add_sensor_issues(issues, "moisture", plant.entities.moisture, states, now)
    _add_sensor_issues(issues, "temperature", plant.entities.temperature, states, now)
    _add_sensor_issues(issues, "humidity", plant.entities.humidity, states, now)
    _add_sensor_issues(issues, "battery", plant.entities.battery, states, now)
    _add_sensor_issues(issues, "conductivity", plant.entities.conductivity, states, now)
    _add_sensor_issues(issues, "brightness", plant.entities.brightness, states, now)

    _add_range_issue(issues, "moisture", plant.entities.moisture, states, thresholds.moisture, "%")
    _add_range_issue(issues, "temperature", plant.entities.temperature, states, thresholds.temperature, "F")
    _add_range_issue(issues, "humidity", plant.entities.humidity, states, thresholds.humidity, "%")
    _add_range_issue(issues, "conductivity", plant.entities.conductivity, states, thresholds.conductivity, "")
    _add_range_issue(issues, "brightness", plant.entities.brightness, states, thresholds.brightness, "lx")
    _add_battery_issue(issues, plant.entities.battery, states, thresholds)

    label = max((issue.severity for issue in issues), default=Severity.GREEN)
    watering_recommended = _watering_recommended(plant, states, thresholds, now)
    summary = _summary(plant, label, issues, watering_recommended)
    return PlantStatus(
        plant_id=plant.id,
        label=label,
        issues=tuple(issues),
        watering_recommended=watering_recommended,
        summary=summary,
    )


def watering_decision(
    plant: PlantConfig,
    states: dict[str, EntityState],
    last_watered_at: datetime | None,
    requested_seconds: int | None = None,
    now: datetime | None = None,
) -> WateringDecision:
    now = _aware(now)
    thresholds = _thresholds_for(plant)
    seconds = min(requested_seconds or plant.watering.max_seconds, plant.watering.max_seconds)
    reasons: list[str] = []

    if not plant.entities.pump:
        reasons.append("No pump entity is mapped for this plant.")

    moisture_state = states.get(plant.entities.moisture or "")
    moisture = _numeric_state(moisture_state)
    if moisture_state is None:
        reasons.append("No moisture sensor is mapped or available.")
    elif _staleness(moisture_state, now) >= Severity.RED:
        reasons.append("Moisture sensor is stale; watering would be blind.")
    elif moisture is None:
        reasons.append("Moisture sensor state is not numeric.")
    elif thresholds.moisture.min_orange is not None and moisture >= thresholds.moisture.min_orange:
        reasons.append("Moisture is not low enough for guarded watering.")

    if last_watered_at:
        cooldown_until = _aware(last_watered_at) + timedelta(hours=plant.watering.cooldown_hours)
        if now < cooldown_until:
            reasons.append(f"Pump cooldown is active until {cooldown_until.isoformat()}.")

    if seconds < 1:
        reasons.append("Watering duration must be at least 1 second.")
    if seconds > plant.watering.max_seconds:
        reasons.append("Requested duration exceeds configured cap.")

    return WateringDecision(
        allowed=not reasons,
        plant_id=plant.id,
        seconds=seconds,
        reasons=tuple(reasons),
    )


def overall_label(statuses: list[PlantStatus]) -> Severity:
    return max((status.label for status in statuses), default=Severity.GREEN)


def _add_plant_entity_issue(
    issues: list[Issue],
    plant: PlantConfig,
    states: dict[str, EntityState],
) -> None:
    if not plant.plant_entity:
        return
    state = states.get(plant.plant_entity)
    if not state:
        issues.append(Issue(Severity.RED, "plant", f"plant entity {plant.plant_entity} is unavailable."))
        return
    value = state.state.lower()
    if value in {"unavailable", "unknown"}:
        issues.append(Issue(Severity.RED, "plant", f"plant entity is {value}."))
    elif value == "problem":
        issues.append(Issue(Severity.ORANGE, "plant", "Home Assistant plant entity reports a problem."))


def _add_sensor_issues(
    issues: list[Issue],
    sensor: str,
    entity_id: str | None,
    states: dict[str, EntityState],
    now: datetime,
) -> None:
    if not entity_id:
        return
    state = states.get(entity_id)
    if not state:
        issues.append(Issue(Severity.RED, sensor, f"{sensor} sensor {entity_id} is unavailable."))
        return
    stale = _staleness_for_sensor(sensor, state, now)
    if stale == Severity.RED:
        issues.append(Issue(Severity.RED, sensor, f"{sensor} has not updated in 24+ hours."))
    elif stale == Severity.ORANGE:
        issues.append(Issue(Severity.ORANGE, sensor, f"{sensor} has not updated in 12+ hours."))


def _add_range_issue(
    issues: list[Issue],
    sensor: str,
    entity_id: str | None,
    states: dict[str, EntityState],
    threshold: ThresholdRange,
    unit: str,
) -> None:
    state = states.get(entity_id or "")
    value = _numeric_state(state)
    if value is None:
        return

    low = _severity_for_low(value, threshold)
    high = _severity_for_high(value, threshold)
    severity = max(low, high)
    if severity == Severity.GREEN:
        return

    direction = "low" if low >= high else "high"
    issues.append(Issue(severity, sensor, f"{sensor} is {direction} at {value:g}{unit}."))


def _add_battery_issue(
    issues: list[Issue],
    entity_id: str | None,
    states: dict[str, EntityState],
    thresholds: SpeciesThresholds,
) -> None:
    value = _numeric_state(states.get(entity_id or ""))
    if value is None:
        return
    if value <= thresholds.battery_red:
        issues.append(Issue(Severity.RED, "battery", f"battery is critically low at {value:g}%."))
    elif value <= thresholds.battery_orange:
        issues.append(Issue(Severity.ORANGE, "battery", f"battery is low at {value:g}%."))


def _watering_recommended(
    plant: PlantConfig,
    states: dict[str, EntityState],
    thresholds: SpeciesThresholds,
    now: datetime,
) -> bool:
    if not plant.entities.pump or not plant.entities.moisture:
        return False
    state = states.get(plant.entities.moisture)
    value = _numeric_state(state)
    return (
        state is not None
        and _staleness(state, now) == Severity.GREEN
        and value is not None
        and thresholds.moisture.min_orange is not None
        and value < thresholds.moisture.min_orange
    )


def _summary(
    plant: PlantConfig,
    label: Severity,
    issues: list[Issue],
    watering_recommended: bool,
) -> str:
    if label == Severity.GREEN and not watering_recommended:
        return f"{plant.location} {plant.name}: green."
    fragments = [issue.message.rstrip(".") for issue in issues[:3]]
    if watering_recommended:
        fragments.append("watering is recommended")
    detail = "; ".join(fragments) if fragments else "check recommended"
    return f"{plant.location} {plant.name}: {label.label} - {detail}."


def _staleness(state: EntityState, now: datetime) -> Severity:
    age = now - _aware(state.last_updated)
    if age >= timedelta(hours=STALE_RED_HOURS):
        return Severity.RED
    if age >= timedelta(hours=STALE_ORANGE_HOURS):
        return Severity.ORANGE
    return Severity.GREEN


def _staleness_for_sensor(sensor: str, state: EntityState, now: datetime) -> Severity:
    if sensor == "battery":
        age = now - _aware(state.last_updated)
        if age >= timedelta(hours=BATTERY_STALE_RED_HOURS):
            return Severity.RED
        if age >= timedelta(hours=BATTERY_STALE_ORANGE_HOURS):
            return Severity.ORANGE
        return Severity.GREEN
    return _staleness(state, now)


def _severity_for_low(value: float, threshold: ThresholdRange) -> Severity:
    if threshold.min_orange is not None and value < threshold.min_orange:
        return Severity.RED
    if threshold.min_green is not None and value < threshold.min_green:
        return Severity.ORANGE
    return Severity.GREEN


def _severity_for_high(value: float, threshold: ThresholdRange) -> Severity:
    if threshold.max_orange is not None and value > threshold.max_orange:
        return Severity.RED
    if threshold.max_green is not None and value > threshold.max_green:
        return Severity.ORANGE
    return Severity.GREEN


def _numeric_state(state: EntityState | None) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _thresholds_for(plant: PlantConfig) -> SpeciesThresholds:
    return plant.thresholds or SPECIES_THRESHOLDS.get(plant.species, DEFAULT_THRESHOLDS)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

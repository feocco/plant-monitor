from __future__ import annotations

from datetime import datetime, timedelta

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
from plant_monitor.policy import (
    aware,
    numeric_state,
    severity_for_high,
    severity_for_low,
    staleness,
    staleness_for_sensor,
    thresholds_for,
)


def evaluate_plant(
    plant: PlantConfig,
    states: dict[str, EntityState],
    now: datetime | None = None,
) -> PlantStatus:
    now = aware(now)
    thresholds = thresholds_for(plant)
    issues: list[Issue] = []

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
    now = aware(now)
    thresholds = thresholds_for(plant)
    seconds = min(requested_seconds or plant.watering.max_seconds, plant.watering.max_seconds)
    reasons: list[str] = []

    if not plant.entities.pump:
        reasons.append("No pump entity is mapped for this plant.")

    moisture_state = states.get(plant.entities.moisture or "")
    moisture = numeric_state(moisture_state)
    if moisture_state is None:
        reasons.append("No moisture sensor is mapped or available.")
    elif staleness(moisture_state, now) >= Severity.RED:
        reasons.append("Moisture sensor is stale; watering would be blind.")
    elif moisture is None:
        reasons.append("Moisture sensor state is not numeric.")
    elif thresholds.moisture.min_orange is not None and moisture >= thresholds.moisture.min_orange:
        reasons.append("Moisture is not low enough for guarded watering.")

    if last_watered_at:
        cooldown_until = aware(last_watered_at) + timedelta(hours=plant.watering.cooldown_hours)
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
    stale = staleness_for_sensor(sensor, state, now)
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
    value = numeric_state(state)
    if value is None:
        return

    low = severity_for_low(value, threshold)
    high = severity_for_high(value, threshold)
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
    value = numeric_state(states.get(entity_id or ""))
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
    value = numeric_state(state)
    return (
        state is not None
        and staleness(state, now) == Severity.GREEN
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


from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from plant_monitor.models import EntityState, Issue, PlantConfig, PlantStatus, Severity
from plant_monitor.policy import (
    BATTERY_STALE_ORANGE_HOURS,
    BATTERY_STALE_RED_HOURS,
    STALE_ORANGE_HOURS,
    STALE_RED_HOURS,
    numeric_state,
    thresholds_for,
)
from plant_monitor.runtime_state import ConditionRecord, RuntimeState, SensorSample

SAMPLE_RETENTION = timedelta(days=14)
POST_WATERING_DRY_SUPPRESSION = timedelta(hours=4)
POST_WATERING_WET_SUPPRESSION = timedelta(hours=6)
SOGGY_HOLD = timedelta(hours=72)
TEMPERATURE_MILD_HOLD = timedelta(hours=24)
TEMPERATURE_EXTREME_HOLD = timedelta(hours=2)
HUMIDITY_HOLD = timedelta(hours=24)
UNAVAILABLE_HOLD = timedelta(hours=12)
HARD_TEMPERATURE_LOW = 50.0
HARD_TEMPERATURE_HIGH = 95.0
HIGH_BRIGHTNESS_LX = 2000.0
UNAVAILABLE_STATES = {"unknown", "unavailable"}

MOISTURE_LOW_HOLDS = {
    "boston_fern": {
        Severity.ORANGE: timedelta(hours=4),
        Severity.RED: timedelta(hours=8),
    },
    "ficus_altissima": {
        Severity.ORANGE: timedelta(hours=12),
        Severity.RED: timedelta(hours=24),
    },
    "golden_pothos": {
        Severity.ORANGE: timedelta(hours=24),
        Severity.RED: timedelta(hours=48),
    },
    "peperomia_jelly": {
        Severity.ORANGE: timedelta(hours=48),
        Severity.RED: timedelta(hours=72),
    },
    "wandering_dude": {
        Severity.ORANGE: timedelta(hours=24),
        Severity.RED: timedelta(hours=48),
    },
    "outdoor_mixed_vegetable_container": {
        Severity.ORANGE: timedelta(hours=6),
        Severity.RED: timedelta(hours=12),
    },
    "outdoor_mixed_annual_hanging_basket": {
        Severity.ORANGE: timedelta(hours=4),
        Severity.RED: timedelta(hours=8),
    },
    "fuchsia_hanging_basket": {
        Severity.ORANGE: timedelta(hours=4),
        Severity.RED: timedelta(hours=8),
    },
}

DEFAULT_MOISTURE_LOW_HOLDS = {
    Severity.ORANGE: timedelta(hours=24),
    Severity.RED: timedelta(hours=48),
}


@dataclass(frozen=True)
class ConditionCandidate:
    plant_id: str
    kind: str
    sensor: str
    severity: Severity
    message: str
    value: float | None
    hold: timedelta
    phone_alert: bool
    watering_candidate: bool = False
    suppressed_until: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.plant_id}:{self.kind}:{self.severity.label}"


def update_conditions(
    plants: list[PlantConfig],
    states: dict[str, EntityState],
    runtime: RuntimeState,
    now: datetime | None = None,
) -> list[ConditionCandidate]:
    now = _aware(now)
    record_samples(plants, states, runtime, now)
    candidates = condition_candidates(plants, states, runtime, now)
    _reconcile_condition_records(runtime, candidates, now)
    _prune_condition_records(runtime, now)
    return candidates


def condition_candidates(
    plants: list[PlantConfig],
    states: dict[str, EntityState],
    runtime: RuntimeState,
    now: datetime | None = None,
) -> list[ConditionCandidate]:
    now = _aware(now)
    candidates: list[ConditionCandidate] = []
    for plant in plants:
        candidates.extend(_freshness_candidates(plant, states, now))
        candidates.extend(_moisture_candidates(plant, states, runtime, now))
        candidates.extend(_temperature_candidates(plant, states, now))
        candidates.extend(_humidity_candidates(plant, states, now))
        candidate = _battery_candidate(plant, states)
        if candidate:
            candidates.append(candidate)
    return candidates


def active_condition_records(runtime: RuntimeState) -> list[ConditionRecord]:
    return [
        record
        for record in runtime.condition_records.values()
        if record.active_since is not None and record.resolved_at is None
    ]


def plant_statuses_from_conditions(
    plants: list[PlantConfig],
    condition_records: list[ConditionRecord],
    watering_allowed: dict[str, bool] | None = None,
) -> list[PlantStatus]:
    watering_allowed = watering_allowed or {}
    records_by_plant: dict[str, list[ConditionRecord]] = {}
    for record in condition_records:
        records_by_plant.setdefault(record.plant_id, []).append(record)

    statuses: list[PlantStatus] = []
    for plant in plants:
        records = records_by_plant.get(plant.id, [])
        issues = tuple(
            Issue(_severity_from_label(record.severity), record.sensor, record.message)
            for record in sorted(records, key=lambda item: item.severity, reverse=True)
        )
        label = max((issue.severity for issue in issues), default=Severity.GREEN)
        watering_recommended = any(
            record.watering_candidate and watering_allowed.get(plant.id, False)
            for record in records
        )
        summary = _status_summary(plant, label, issues, watering_recommended)
        statuses.append(
            PlantStatus(
                plant_id=plant.id,
                label=label,
                issues=issues,
                watering_recommended=watering_recommended,
                summary=summary,
            )
        )
    return statuses


def due_phone_conditions(
    runtime: RuntimeState,
    plant_id: str,
    repeat_hours: int,
    now: datetime | None = None,
) -> list[ConditionRecord]:
    now = _aware(now)
    repeat = timedelta(hours=repeat_hours)
    due: list[ConditionRecord] = []
    for record in active_condition_records(runtime):
        if record.plant_id != plant_id or not record.phone_alert:
            continue
        if record.suppressed_until and now < record.suppressed_until.astimezone(UTC):
            continue
        if record.last_notified_at is None:
            due.append(record)
        elif now - record.last_notified_at.astimezone(UTC) >= repeat:
            due.append(record)
    return due


def mark_notified(records: list[ConditionRecord], now: datetime | None = None) -> None:
    now = _aware(now)
    for record in records:
        record.last_notified_at = now


def record_samples(
    plants: list[PlantConfig],
    states: dict[str, EntityState],
    runtime: RuntimeState,
    now: datetime | None = None,
) -> None:
    now = _aware(now)
    existing = {
        (sample.entity_id, sample.timestamp.isoformat(), sample.value)
        for sample in runtime.samples
    }
    for plant in plants:
        for sensor, entity_id in _plant_sensor_entities(plant):
            state = states.get(entity_id)
            value = numeric_state(state)
            if state is None or value is None:
                continue
            timestamp = _aware(state.freshness_timestamp)
            key = (entity_id, timestamp.isoformat(), value)
            if key in existing:
                continue
            runtime.samples.append(
                SensorSample(
                    timestamp=timestamp,
                    plant_id=plant.id,
                    sensor=sensor,
                    entity_id=entity_id,
                    value=value,
                )
            )
            existing.add(key)
    cutoff = now - SAMPLE_RETENTION
    runtime.samples = [
        sample for sample in runtime.samples if _aware(sample.timestamp) >= cutoff
    ]


def _freshness_candidates(
    plant: PlantConfig,
    states: dict[str, EntityState],
    now: datetime,
) -> list[ConditionCandidate]:
    candidates: list[ConditionCandidate] = []
    for sensor, entity_id in _plant_sensor_entities(plant):
        state = states.get(entity_id)
        kind = f"{sensor}_unavailable"
        if state is None or state.state.lower() in UNAVAILABLE_STATES:
            status = (
                "is missing from the Home Assistant state snapshot"
                if state is None
                else f"reported {state.state}"
            )
            candidates.append(
                ConditionCandidate(
                    plant_id=plant.id,
                    kind=kind,
                    sensor=sensor,
                    severity=Severity.RED,
                    message=f"{sensor} sensor {entity_id} {status}.",
                    value=None,
                    hold=UNAVAILABLE_HOLD,
                    phone_alert=True,
                )
            )
            continue
        age_hours = (now - _aware(state.freshness_timestamp)).total_seconds() / 3600
        orange_hours = (
            BATTERY_STALE_ORANGE_HOURS if sensor == "battery" else STALE_ORANGE_HOURS
        )
        red_hours = BATTERY_STALE_RED_HOURS if sensor == "battery" else STALE_RED_HOURS
        if sensor == "battery" and _has_fresh_non_battery_sensor(plant, states, now):
            continue
        if age_hours >= red_hours:
            candidates.append(
                ConditionCandidate(
                    plant_id=plant.id,
                    kind=f"{sensor}_stale",
                    sensor=sensor,
                    severity=Severity.RED,
                    message=_stale_message(sensor, red_hours),
                    value=None,
                    hold=timedelta(0),
                    phone_alert=True,
                )
            )
        elif age_hours >= orange_hours:
            candidates.append(
                ConditionCandidate(
                    plant_id=plant.id,
                    kind=f"{sensor}_stale",
                    sensor=sensor,
                    severity=Severity.ORANGE,
                    message=_stale_message(sensor, orange_hours),
                    value=None,
                    hold=timedelta(0),
                    phone_alert=False,
                )
            )
    return candidates


def _has_fresh_non_battery_sensor(
    plant: PlantConfig,
    states: dict[str, EntityState],
    now: datetime,
) -> bool:
    for sensor, entity_id in _plant_sensor_entities(plant):
        if sensor == "battery":
            continue
        state = states.get(entity_id)
        if state is None:
            continue
        age = now - _aware(state.freshness_timestamp)
        if age < timedelta(hours=STALE_ORANGE_HOURS):
            return True
    return False


def _stale_message(sensor: str, hours: float) -> str:
    if sensor == "battery":
        return f"{sensor} has not updated in {hours / 24:g}+ days."
    return f"{sensor} has not updated in {hours:g}+ hours."


def _moisture_candidates(
    plant: PlantConfig,
    states: dict[str, EntityState],
    runtime: RuntimeState,
    now: datetime,
) -> list[ConditionCandidate]:
    state = states.get(plant.entities.moisture or "")
    value = numeric_state(state)
    if value is None:
        return []

    thresholds = thresholds_for(plant).moisture
    candidates: list[ConditionCandidate] = []
    if thresholds.min_orange is not None and value < thresholds.min_orange:
        candidates.append(
            _moisture_low_candidate(plant, value, Severity.RED, runtime, now)
        )
    elif thresholds.min_green is not None and value < thresholds.min_green:
        candidates.append(
            _moisture_low_candidate(plant, value, Severity.ORANGE, runtime, now)
        )

    max_orange = thresholds.max_orange
    max_green = thresholds.max_green
    if max_orange is not None and value > max_orange:
        candidates.append(_moisture_high_candidate(plant, value, Severity.RED, runtime))
    elif max_green is not None and value > max_green:
        candidates.append(
            _moisture_high_candidate(plant, value, Severity.ORANGE, runtime)
        )
    return candidates


def _moisture_low_candidate(
    plant: PlantConfig,
    value: float,
    severity: Severity,
    runtime: RuntimeState,
    now: datetime,
) -> ConditionCandidate:
    holds = MOISTURE_LOW_HOLDS.get(plant.species, DEFAULT_MOISTURE_LOW_HOLDS)
    suppressed_until = _post_water_suppression(
        runtime,
        plant.id,
        POST_WATERING_DRY_SUPPRESSION,
    )
    return ConditionCandidate(
        plant_id=plant.id,
        kind="moisture_low",
        sensor="moisture",
        severity=severity,
        message=f"moisture has stayed low at {value:g}%.",
        value=value,
        hold=holds[severity],
        phone_alert=severity == Severity.RED,
        watering_candidate=severity == Severity.RED
        and not (suppressed_until and now < suppressed_until),
        suppressed_until=suppressed_until,
    )


def _moisture_high_candidate(
    plant: PlantConfig,
    value: float,
    severity: Severity,
    runtime: RuntimeState,
) -> ConditionCandidate:
    return ConditionCandidate(
        plant_id=plant.id,
        kind="moisture_high",
        sensor="moisture",
        severity=severity,
        message=f"soil has stayed very wet at {value:g}%.",
        value=value,
        hold=SOGGY_HOLD,
        phone_alert=severity == Severity.RED,
        suppressed_until=_post_water_suppression(
            runtime,
            plant.id,
            POST_WATERING_WET_SUPPRESSION,
        ),
    )


def _temperature_candidates(
    plant: PlantConfig,
    states: dict[str, EntityState],
    now: datetime,
) -> list[ConditionCandidate]:
    state = states.get(plant.entities.temperature or "")
    value = numeric_state(state)
    if value is None:
        return []

    brightness = numeric_state(states.get(plant.entities.brightness or ""))
    sun_driven = brightness is not None and brightness >= HIGH_BRIGHTNESS_LX
    candidates: list[ConditionCandidate] = []
    if value < HARD_TEMPERATURE_LOW:
        candidates.append(
            _temperature_candidate(
                plant,
                "temperature_extreme_low",
                Severity.RED,
                value,
                TEMPERATURE_EXTREME_HOLD,
                True,
            )
        )
    elif value > HARD_TEMPERATURE_HIGH:
        candidates.append(
            _temperature_candidate(
                plant,
                "temperature_extreme_high",
                Severity.RED,
                value,
                TEMPERATURE_EXTREME_HOLD,
                not sun_driven,
            )
        )
    else:
        thresholds = thresholds_for(plant).temperature
        if thresholds.min_green is not None and value < thresholds.min_green:
            candidates.append(
                _temperature_candidate(
                    plant,
                    "temperature_low",
                    Severity.ORANGE,
                    value,
                    TEMPERATURE_MILD_HOLD,
                    False,
                )
            )
        elif thresholds.max_green is not None and value > thresholds.max_green:
            candidates.append(
                _temperature_candidate(
                    plant,
                    "temperature_high",
                    Severity.ORANGE,
                    value,
                    TEMPERATURE_MILD_HOLD,
                    False,
                    sun_driven=sun_driven,
                )
            )
    return candidates


def _temperature_candidate(
    plant: PlantConfig,
    kind: str,
    severity: Severity,
    value: float,
    hold: timedelta,
    phone_alert: bool,
    *,
    sun_driven: bool = False,
) -> ConditionCandidate:
    detail = " during bright sun" if sun_driven else ""
    direction = "low" if "low" in kind else "high"
    return ConditionCandidate(
        plant_id=plant.id,
        kind=kind,
        sensor="temperature",
        severity=severity,
        message=f"temperature has stayed {direction} at {value:g}F{detail}.",
        value=value,
        hold=hold,
        phone_alert=phone_alert,
    )


def _humidity_candidates(
    plant: PlantConfig,
    states: dict[str, EntityState],
    now: datetime,
) -> list[ConditionCandidate]:
    del now
    state = states.get(plant.entities.humidity or "")
    value = numeric_state(state)
    if value is None:
        return []

    thresholds = thresholds_for(plant).humidity
    if thresholds.min_orange is not None and value < thresholds.min_orange:
        severity = Severity.RED
        direction = "low"
    elif thresholds.min_green is not None and value < thresholds.min_green:
        severity = Severity.ORANGE
        direction = "low"
    elif thresholds.max_orange is not None and value > thresholds.max_orange:
        severity = Severity.RED
        direction = "high"
    elif thresholds.max_green is not None and value > thresholds.max_green:
        severity = Severity.ORANGE
        direction = "high"
    else:
        return []

    return [
        ConditionCandidate(
            plant_id=plant.id,
            kind=f"humidity_{direction}",
            sensor="humidity",
            severity=severity,
            message=f"humidity has stayed {direction} at {value:g}%.",
            value=value,
            hold=HUMIDITY_HOLD,
            phone_alert=False,
        )
    ]


def _battery_candidate(
    plant: PlantConfig,
    states: dict[str, EntityState],
) -> ConditionCandidate | None:
    state = states.get(plant.entities.battery or "")
    value = numeric_state(state)
    if value is None:
        return None

    thresholds = thresholds_for(plant)
    if value <= thresholds.battery_red:
        return ConditionCandidate(
            plant_id=plant.id,
            kind="battery_low",
            sensor="battery",
            severity=Severity.RED,
            message=f"battery is critically low at {value:g}%.",
            value=value,
            hold=timedelta(0),
            phone_alert=True,
        )
    if value <= thresholds.battery_orange:
        return ConditionCandidate(
            plant_id=plant.id,
            kind="battery_low",
            sensor="battery",
            severity=Severity.ORANGE,
            message=f"battery is low at {value:g}%.",
            value=value,
            hold=timedelta(0),
            phone_alert=False,
        )
    return None


def _reconcile_condition_records(
    runtime: RuntimeState,
    candidates: list[ConditionCandidate],
    now: datetime,
) -> None:
    candidate_by_key = {candidate.key: candidate for candidate in candidates}

    for key, record in list(runtime.condition_records.items()):
        if key not in candidate_by_key and record.resolved_at is None:
            record.resolved_at = now

    for candidate in candidates:
        record = runtime.condition_records.get(candidate.key)
        if record is None or record.resolved_at is not None:
            record = ConditionRecord(
                key=candidate.key,
                plant_id=candidate.plant_id,
                kind=candidate.kind,
                sensor=candidate.sensor,
                severity=candidate.severity.label,
                message=candidate.message,
                first_seen_at=now,
                last_seen_at=now,
                last_value=candidate.value,
                phone_alert=candidate.phone_alert,
                watering_candidate=candidate.watering_candidate,
                suppressed_until=candidate.suppressed_until,
            )
            runtime.condition_records[candidate.key] = record
        else:
            record.last_seen_at = now
            record.last_value = candidate.value
            record.message = candidate.message
            record.phone_alert = candidate.phone_alert
            record.watering_candidate = candidate.watering_candidate
            record.suppressed_until = candidate.suppressed_until

        if (
            record.active_since is None
            and now - record.first_seen_at.astimezone(UTC) >= candidate.hold
        ):
            record.active_since = now


def _prune_condition_records(runtime: RuntimeState, now: datetime) -> None:
    cutoff = now - SAMPLE_RETENTION
    runtime.condition_records = {
        key: record
        for key, record in runtime.condition_records.items()
        if record.resolved_at is None or record.resolved_at.astimezone(UTC) >= cutoff
    }


def _plant_sensor_entities(plant: PlantConfig) -> tuple[tuple[str, str], ...]:
    return tuple(
        (sensor, entity_id)
        for sensor, entity_id in (
            ("moisture", plant.entities.moisture),
            ("temperature", plant.entities.temperature),
            ("humidity", plant.entities.humidity),
            ("battery", plant.entities.battery),
            ("conductivity", plant.entities.conductivity),
            ("brightness", plant.entities.brightness),
        )
        if entity_id
    )


def _post_water_suppression(
    runtime: RuntimeState,
    plant_id: str,
    duration: timedelta,
) -> datetime | None:
    watered_at = runtime.last_watered_at.get(plant_id)
    if watered_at is None:
        return None
    return watered_at.astimezone(UTC) + duration


def _status_summary(
    plant: PlantConfig,
    label: Severity,
    issues: tuple[Issue, ...],
    watering_recommended: bool,
) -> str:
    if label == Severity.GREEN and not watering_recommended:
        return f"{plant.location} {plant.name}: green."
    fragments = [issue.message.rstrip(".") for issue in issues[:3]]
    if watering_recommended:
        fragments.append("watering is recommended")
    detail = "; ".join(fragments) if fragments else "check recommended"
    return f"{plant.location} {plant.name}: {label.label} - {detail}."


def _severity_from_label(value: str) -> Severity:
    return Severity[value.upper()]


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

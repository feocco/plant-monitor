from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from plant_monitor.condition_engine import (
    POST_WATERING_DRY_SUPPRESSION,
    POST_WATERING_WET_SUPPRESSION,
)
from plant_monitor.ha import HomeAssistantClient
from plant_monitor.models import EntityState, PlantConfig, ServiceConfig
from plant_monitor.notify import Notifier
from plant_monitor.rules import watering_decision
from plant_monitor.runtime_state import RuntimeState, ScheduledJob

LOGGER = logging.getLogger(__name__)
WATERING_LOOKBACK_DELAYS = (timedelta(hours=1), timedelta(hours=4))
WATERING_CHANGE_THRESHOLD = 1.0
WATERING_LOOKBACK_JOB_KIND = "watering_lookback"


@dataclass(frozen=True)
class SensorReading:
    sensor: str
    entity_id: str
    value: float | None
    last_updated: datetime | None


class WateringService:
    def __init__(
        self,
        config: ServiceConfig,
        plants: list[PlantConfig],
        ha: HomeAssistantClient,
        state: RuntimeState,
        notifier: Notifier,
        get_states: Callable[[], dict[str, EntityState]],
        set_states: Callable[[dict[str, EntityState]], None],
    ) -> None:
        self.config = config
        self.plants = plants
        self.ha = ha
        self.state = state
        self.notifier = notifier
        self._get_states = get_states
        self._set_states = set_states
        self._plant_by_id = {plant.id: plant for plant in plants}

    async def handle_water_request(self, plant_id: str, seconds: int | None) -> tuple[int, dict]:
        plant = self._plant_by_id.get(plant_id)
        if not plant:
            return 404, {"allowed": False, "reasons": ["Unknown plant id."]}

        decision = watering_decision(
            plant,
            self._get_states(),
            self.state.last_watered_at.get(plant.id),
            requested_seconds=seconds,
        )
        if not decision.allowed:
            message = "Watering blocked: " + "; ".join(decision.reasons)
            await self.notifier.send_watering_result(plant, message)
            return 409, {"allowed": False, "reasons": list(decision.reasons)}

        if plant.entities.pump is None:
            return 409, {"allowed": False, "reasons": ["No pump entity is mapped for this plant."]}
        await self._run_pump(plant.entities.pump, decision.seconds)
        watered_at = datetime.now(UTC)
        baseline = _watering_snapshot(plant, self._get_states())
        self.state.last_watered_at[plant.id] = watered_at
        self._apply_watering_suppression(plant.id, watered_at)
        self._schedule_watering_lookbacks(plant, watered_at, baseline)
        self.state.save(self.config.state_path)
        LOGGER.info(
            "Watering event recorded: plant=%s pump=%s seconds=%s baseline=%s",
            plant.id,
            plant.entities.pump,
            decision.seconds,
            _format_snapshot_for_log(baseline),
        )
        message = f"Watered for {decision.seconds} seconds."
        await self.notifier.send_watering_result(plant, message)
        return 200, {"allowed": True, "seconds": decision.seconds}

    async def run_due_scheduled_jobs(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        due_jobs = [
            job
            for job in self.state.scheduled_jobs
            if job.due_at.astimezone(UTC) <= now.astimezone(UTC)
        ]
        if not due_jobs:
            return

        completed_job_ids: list[str] = []
        for job in due_jobs:
            try:
                await self._run_scheduled_job(job)
            except Exception:
                LOGGER.exception(
                    "Scheduled job failed; will retry: job=%s kind=%s plant=%s due_at=%s",
                    job.id,
                    job.kind,
                    job.plant_id,
                    job.due_at.isoformat(),
                )
            else:
                completed_job_ids.append(job.id)

        if completed_job_ids:
            for job_id in completed_job_ids:
                self.state.remove_scheduled_job(job_id)
            self.state.save(self.config.state_path)

    async def _run_pump(self, entity_id: str, seconds: int) -> None:
        await self.ha.call_service("switch", "turn_on", {"entity_id": entity_id})
        try:
            await asyncio.sleep(seconds)
        finally:
            await self.ha.call_service("switch", "turn_off", {"entity_id": entity_id})

    def _apply_watering_suppression(self, plant_id: str, watered_at: datetime) -> None:
        for record in self.state.condition_records.values():
            if record.plant_id != plant_id:
                continue
            if record.kind == "moisture_low":
                record.suppressed_until = watered_at + POST_WATERING_DRY_SUPPRESSION
                record.last_notified_at = None
            elif record.kind == "moisture_high":
                record.suppressed_until = watered_at + POST_WATERING_WET_SUPPRESSION
                record.last_notified_at = None

    def _schedule_watering_lookbacks(
        self,
        plant: PlantConfig,
        watered_at: datetime,
        baseline: list[SensorReading],
    ) -> None:
        for delay in WATERING_LOOKBACK_DELAYS:
            job = _watering_lookback_job(plant, watered_at, delay, baseline)
            self.state.upsert_scheduled_job(job)
            LOGGER.info(
                "Scheduled watering lookback: plant=%s delay=%s due_at=%s job=%s",
                plant.id,
                _format_duration(delay),
                job.due_at.isoformat(),
                job.id,
            )

    async def _run_scheduled_job(self, job: ScheduledJob) -> None:
        if job.kind != WATERING_LOOKBACK_JOB_KIND:
            LOGGER.warning(
                "Dropping unknown scheduled job kind: job=%s kind=%s",
                job.id,
                job.kind,
            )
            return

        plant = self._plant_by_id.get(job.plant_id)
        if plant is None:
            LOGGER.warning(
                "Dropping scheduled job for unknown plant: job=%s plant=%s",
                job.id,
                job.plant_id,
            )
            return

        watered_at, delay, baseline = _watering_lookback_from_payload(job)
        await self._send_watering_lookback(plant, watered_at, baseline, delay)

    async def _send_watering_lookback(
        self,
        plant: PlantConfig,
        watered_at: datetime,
        baseline: list[SensorReading],
        delay: timedelta,
    ) -> None:
        states = await self.ha.get_states()
        self._set_states(states)
        current = _watering_snapshot(plant, states)
        message = _watering_lookback_message(baseline, current, delay)
        LOGGER.info(
            "Watering lookback complete: plant=%s watered_at=%s delay=%s result=%s current=%s",
            plant.id,
            watered_at.isoformat(),
            _format_duration(delay),
            message.replace("\n", " | "),
            _format_snapshot_for_log(current),
        )
        await self.notifier.send_watering_lookback(plant, message)


def _watering_snapshot(plant: PlantConfig, states: dict[str, EntityState]) -> list[SensorReading]:
    readings: list[SensorReading] = []
    for sensor, entity_id in (
        ("moisture", plant.entities.moisture),
        ("humidity", plant.entities.humidity),
    ):
        if not entity_id:
            continue
        state = states.get(entity_id)
        readings.append(
            SensorReading(
                sensor=sensor,
                entity_id=entity_id,
                value=_float_state(state),
                last_updated=state.last_updated if state else None,
            )
        )
    return readings


def _watering_lookback_job(
    plant: PlantConfig,
    watered_at: datetime,
    delay: timedelta,
    baseline: list[SensorReading],
) -> ScheduledJob:
    watered_at = watered_at.astimezone(UTC)
    due_at = watered_at + delay
    delay_seconds = int(delay.total_seconds())
    job_id = f"{WATERING_LOOKBACK_JOB_KIND}:{plant.id}:{watered_at.isoformat()}:{delay_seconds}"
    return ScheduledJob(
        id=job_id,
        kind=WATERING_LOOKBACK_JOB_KIND,
        plant_id=plant.id,
        due_at=due_at,
        payload={
            "watered_at": watered_at.isoformat(),
            "delay_seconds": delay_seconds,
            "baseline": [_sensor_reading_payload(reading) for reading in baseline],
        },
    )


def _watering_lookback_from_payload(
    job: ScheduledJob,
) -> tuple[datetime, timedelta, list[SensorReading]]:
    watered_at_value = job.payload.get("watered_at")
    watered_at = (
        datetime.fromisoformat(str(watered_at_value)).astimezone(UTC)
        if watered_at_value
        else job.due_at.astimezone(UTC)
    )
    delay_seconds = job.payload.get("delay_seconds")
    if delay_seconds is None:
        delay = job.due_at.astimezone(UTC) - watered_at
    else:
        delay = timedelta(seconds=float(delay_seconds))

    baseline_payload = job.payload.get("baseline") or []
    baseline = [
        _sensor_reading_from_payload(item)
        for item in baseline_payload
        if isinstance(item, dict)
    ]
    return watered_at, delay, baseline


def _sensor_reading_payload(reading: SensorReading) -> dict[str, object]:
    return {
        "sensor": reading.sensor,
        "entity_id": reading.entity_id,
        "value": reading.value,
        "last_updated": reading.last_updated.isoformat() if reading.last_updated else None,
    }


def _sensor_reading_from_payload(payload: dict[object, object]) -> SensorReading:
    value = payload.get("value")
    last_updated = payload.get("last_updated")
    return SensorReading(
        sensor=str(payload["sensor"]),
        entity_id=str(payload["entity_id"]),
        value=None if value is None else float(value),
        last_updated=datetime.fromisoformat(str(last_updated)) if last_updated else None,
    )


def _watering_lookback_message(
    baseline: list[SensorReading],
    current: list[SensorReading],
    delay: timedelta,
) -> str:
    current_by_sensor = {reading.sensor: reading for reading in current}
    lines = [f"Watering research after {_format_duration(delay)}:"]
    changed = False

    for before in baseline:
        after = current_by_sensor.get(before.sensor)
        if not after or before.value is None or after.value is None:
            lines.append(f"- {before.sensor}: unavailable for comparison")
            continue
        delta = after.value - before.value
        changed = changed or abs(delta) >= WATERING_CHANGE_THRESHOLD
        lines.append(
            f"- {before.sensor}: {_format_value(before.value)} -> {_format_value(after.value)} ({delta:+.1f})"
        )

    if len(lines) == 1:
        lines.append("- no moisture or humidity sensor was mapped")
    elif changed:
        lines.append("Result: measurable sensor movement detected.")
    else:
        lines.append("Result: no clear movement detected yet.")
    return "\n".join(lines)


def _format_snapshot_for_log(readings: list[SensorReading]) -> str:
    if not readings:
        return "none"
    parts = []
    for reading in readings:
        value = "unknown" if reading.value is None else _format_value(reading.value)
        updated = "unknown" if reading.last_updated is None else reading.last_updated.isoformat()
        parts.append(f"{reading.sensor}={value} updated={updated} entity={reading.entity_id}")
    return "; ".join(parts)


def _format_duration(value: timedelta) -> str:
    total_minutes = max(1, int(value.total_seconds() // 60))
    days, remainder = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    return " ".join(parts) or "0m"


def _format_value(value: float) -> str:
    return f"{value:.1f}"


def _float_state(state: EntityState | None) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None

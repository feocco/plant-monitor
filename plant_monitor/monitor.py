from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from plant_monitor.ha import HomeAssistantClient, parse_entity_state
from plant_monitor.models import EntityState, PlantConfig, PlantStatus, ServiceConfig, Severity
from plant_monitor.notify import Notifier, SNOOZE_ACTION_PREFIX, WATER_ACTION_PREFIX, should_send_urgent
from plant_monitor.rules import evaluate_plant, watering_decision
from plant_monitor.runtime_state import RuntimeState, ScheduledJob
from plant_monitor.web import CallbackServer

LOGGER = logging.getLogger(__name__)
WATERING_LOOKBACK_DELAYS = (timedelta(hours=1), timedelta(hours=4))
WATERING_CHANGE_THRESHOLD = 1.0
WATERING_LOOKBACK_JOB_KIND = "watering_lookback"
SCHEDULED_JOB_POLL_SECONDS = 60


@dataclass(frozen=True)
class SensorReading:
    sensor: str
    entity_id: str
    value: float | None
    last_updated: datetime | None


class PlantMonitor:
    def __init__(
        self,
        config: ServiceConfig,
        plants: list[PlantConfig],
        ha: HomeAssistantClient,
        state: RuntimeState,
    ) -> None:
        self.config = config
        self.plants = plants
        self.ha = ha
        self.state = state
        self.states: dict[str, EntityState] = {}
        self.notifier = Notifier(
            config.plants_dashboard_url,
            service_url=config.homelab_functions_url,
            token=config.homelab_functions_token,
        )
        self.ha.add_event_handler(self.handle_event)
        self.callback_server = CallbackServer(
            config.service_host,
            config.service_port,
            config.callback_token,
            self.handle_water_request,
        )
        self._plant_by_id = {plant.id: plant for plant in plants}

    async def run(self) -> None:
        while True:
            try:
                await self._run_connected()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Monitor crashed; reconnecting soon")
                await asyncio.sleep(15)

    async def _run_connected(self) -> None:
        await self.ha.connect()
        self.states = await self.ha.get_states()
        await self.ha.subscribe_events("state_changed")
        await self.ha.subscribe_events("mobile_app_notification_action")
        await self.callback_server.start()
        if self.state.last_dry_run is not False and not self.config.dry_run and self.state.last_alert_label:
            LOGGER.info("Clearing previous alert memory before first real notification run")
            self.state.last_alert_label.clear()
        self.state.last_dry_run = self.config.dry_run
        statuses = await self.evaluate_and_notify()
        self._log_startup_health(statuses)
        ha_closed_task = asyncio.create_task(self.ha.wait_closed(), name="ha-websocket-watch")
        tasks = [
            ha_closed_task,
            asyncio.create_task(self._reconcile_loop(), name="plant-reconcile-loop"),
            asyncio.create_task(self._weekly_loop(), name="plant-weekly-loop"),
            asyncio.create_task(self._scheduled_job_loop(), name="plant-scheduled-job-loop"),
        ]

        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if ha_closed_task in done:
                LOGGER.info("Home Assistant WebSocket closed; reconnecting")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.callback_server.stop()
            await self.ha.close()

    async def handle_event(self, event: dict) -> None:
        event_type = event.get("event_type")
        data = event.get("data") or {}
        if event_type == "state_changed":
            new_state = data.get("new_state")
            if new_state:
                parsed = parse_entity_state(new_state)
                self.states[parsed.entity_id] = parsed
                if parsed.entity_id in self._watched_entities():
                    await self.evaluate_and_notify()
        elif event_type == "mobile_app_notification_action":
            action = data.get("action")
            if isinstance(action, str) and action.startswith(WATER_ACTION_PREFIX):
                plant_id = action.removeprefix(WATER_ACTION_PREFIX)
                await self.handle_water_request(plant_id, None)
            elif isinstance(action, str) and action.startswith(SNOOZE_ACTION_PREFIX):
                plant_id = action.removeprefix(SNOOZE_ACTION_PREFIX)
                await self.handle_snooze_request(plant_id)

    async def evaluate_and_notify(self) -> list[PlantStatus]:
        statuses = [evaluate_plant(plant, self.states) for plant in self.plants]
        now = datetime.now(UTC)
        for plant, status in zip(self.plants, statuses, strict=True):
            previous = self.state.last_alert_label.get(plant.id)
            current = _alert_key(status)
            if self._is_snoozed(plant.id):
                continue
            if should_send_urgent(status) and self._should_send_alert(plant.id, previous, current, now):
                await self.notifier.send_urgent(plant, status)
                if self.config.dry_run:
                    LOGGER.info("DRY_RUN alert not recorded as sent for %s", plant.id)
                else:
                    self.state.last_alert_label[plant.id] = current
                    self.state.last_alert_sent_at[plant.id] = now
            elif status.label == Severity.GREEN and not status.watering_recommended:
                self.state.last_alert_label.pop(plant.id, None)
                self.state.last_alert_sent_at.pop(plant.id, None)
                self.state.alert_snoozed_until.pop(plant.id, None)
        self.state.save(self.config.state_path)
        return statuses

    async def handle_snooze_request(self, plant_id: str) -> tuple[int, dict]:
        plant = self._plant_by_id.get(plant_id)
        if not plant:
            return 404, {"allowed": False, "reasons": ["Unknown plant id."]}
        until = datetime.now(UTC) + timedelta(hours=self.config.alert_snooze_hours)
        self.state.alert_snoozed_until[plant_id] = until
        self.state.save(self.config.state_path)
        await self.notifier.send_alert_snoozed(
            plant,
            f"Alerts delayed until {until.astimezone().strftime('%Y-%m-%d %H:%M %Z')}.",
        )
        return 200, {"snoozed_until": until.isoformat()}

    async def handle_water_request(self, plant_id: str, seconds: int | None) -> tuple[int, dict]:
        plant = self._plant_by_id.get(plant_id)
        if not plant:
            return 404, {"allowed": False, "reasons": ["Unknown plant id."]}

        decision = watering_decision(
            plant,
            self.states,
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
        baseline = _watering_snapshot(plant, self.states)
        self.state.last_watered_at[plant.id] = watered_at
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

    async def _run_pump(self, entity_id: str, seconds: int) -> None:
        await self.ha.call_service("switch", "turn_on", {"entity_id": entity_id})
        try:
            await asyncio.sleep(seconds)
        finally:
            await self.ha.call_service("switch", "turn_off", {"entity_id": entity_id})

    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            self.states = await self.ha.get_states()
            await self.evaluate_and_notify()

    async def _weekly_loop(self) -> None:
        zone = ZoneInfo(self.config.timezone)
        while True:
            now = datetime.now(zone)
            weekly_key = f"{now.isocalendar().year}-W{now.isocalendar().week}"
            if (
                now.weekday() == 4
                and now.time() >= time(hour=16)
                and self.state.last_weekly_key != weekly_key
            ):
                statuses = [evaluate_plant(plant, self.states, now) for plant in self.plants]
                await self.notifier.send_weekly_digest(self.plants, statuses)
                self.state.last_weekly_key = weekly_key
                self.state.save(self.config.state_path)
            await asyncio.sleep(300)

    async def _scheduled_job_loop(self) -> None:
        while True:
            await self._run_due_scheduled_jobs()
            await asyncio.sleep(SCHEDULED_JOB_POLL_SECONDS)

    def _watched_entities(self) -> set[str]:
        entities: set[str] = set()
        for plant in self.plants:
            entities.update(
                entity
                for entity in (
                    plant.entities.moisture,
                    plant.entities.temperature,
                    plant.entities.humidity,
                    plant.entities.battery,
                    plant.entities.conductivity,
                    plant.entities.brightness,
                    plant.entities.pump,
                )
                if entity
            )
        return entities

    def _is_snoozed(self, plant_id: str) -> bool:
        until = self.state.alert_snoozed_until.get(plant_id)
        if until is None:
            return False
        if datetime.now(UTC) < until.astimezone(UTC):
            return True
        self.state.alert_snoozed_until.pop(plant_id, None)
        return False

    def _should_send_alert(
        self,
        plant_id: str,
        previous_key: str | None,
        current_key: str,
        now: datetime,
    ) -> bool:
        if previous_key != current_key:
            return True
        sent_at = self.state.last_alert_sent_at.get(plant_id)
        if sent_at is None:
            return True
        return now - sent_at.astimezone(UTC) >= timedelta(hours=self.config.alert_repeat_hours)

    def _log_startup_health(self, statuses: list[PlantStatus]) -> None:
        counts = _status_counts(statuses)
        LOGGER.info(
            "Startup plant health: green=%s orange=%s red=%s next_alert=%s",
            counts[Severity.GREEN],
            counts[Severity.ORANGE],
            counts[Severity.RED],
            _next_alert_summary(self.plants, statuses, self.state, self.config.alert_repeat_hours),
        )

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

    async def _run_due_scheduled_jobs(self, now: datetime | None = None) -> None:
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
        self.states = await self.ha.get_states()
        current = _watering_snapshot(plant, self.states)
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


def _alert_key(status: PlantStatus) -> str:
    issue_key = "|".join(sorted(_issue_key(issue) for issue in status.issues))
    watering = "water" if status.watering_recommended else "no-water"
    return f"{status.label.label}:{watering}:{issue_key}"


def _status_counts(statuses: list[PlantStatus]) -> dict[Severity, int]:
    return {
        Severity.GREEN: sum(1 for status in statuses if status.label == Severity.GREEN),
        Severity.ORANGE: sum(1 for status in statuses if status.label == Severity.ORANGE),
        Severity.RED: sum(1 for status in statuses if status.label == Severity.RED),
    }


def _next_alert_summary(
    plants: list[PlantConfig],
    statuses: list[PlantStatus],
    state: RuntimeState,
    repeat_hours: int,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    next_times: list[datetime] = []
    for plant, status in zip(plants, statuses, strict=True):
        if not should_send_urgent(status):
            continue

        snoozed_until = state.alert_snoozed_until.get(plant.id)
        if snoozed_until and now < snoozed_until.astimezone(UTC):
            next_times.append(snoozed_until.astimezone(UTC))
            continue

        sent_at = state.last_alert_sent_at.get(plant.id)
        if sent_at is None:
            next_times.append(now)
            continue

        next_times.append(sent_at.astimezone(UTC) + timedelta(hours=repeat_hours))

    if not next_times:
        return "none"
    next_alert_at = min(next_times)
    if next_alert_at <= now:
        return "now"
    return f"in {_format_duration(next_alert_at - now)}"


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


def _format_value(value: float) -> str:
    return f"{value:.1f}"


def _float_state(state: EntityState | None) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def _issue_key(issue) -> str:
    message = issue.message.lower()
    if "has not updated in 24+" in message:
        return f"{issue.sensor}:stale_red"
    if "has not updated in 12+" in message:
        return f"{issue.sensor}:stale_orange"
    if "critically low" in message:
        return f"{issue.sensor}:critical_low"
    if "is low" in message:
        return f"{issue.sensor}:low"
    if " is high " in message:
        return f"{issue.sensor}:high"
    if "unavailable" in message:
        return f"{issue.sensor}:unavailable"
    if issue.sensor == "plant":
        return "plant:problem"
    return f"{issue.sensor}:{issue.severity.label}"

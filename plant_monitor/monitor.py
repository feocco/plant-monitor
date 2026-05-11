from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from plant_monitor.condition_engine import active_condition_records, mark_notified, plant_statuses_from_conditions, update_conditions
from plant_monitor.ha import HomeAssistantClient, parse_entity_state
from plant_monitor.models import EntityState, PlantConfig, PlantStatus, ServiceConfig, Severity
from plant_monitor.llm_text import rewrite_notification_text
from plant_monitor.notification_planner import NotificationPlanner
from plant_monitor.notify import (
    Notifier,
    SNOOZE_ACTION_PREFIX,
    WATER_ACTION_PREFIX,
    should_send_urgent,
    urgent_message,
)
from plant_monitor.runtime_state import RuntimeState
from plant_monitor.watering import WateringService, watering_decision
from plant_monitor.web import CallbackServer

LOGGER = logging.getLogger(__name__)
SCHEDULED_JOB_POLL_SECONDS = 60


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
        self.watering = WateringService(
            config,
            plants,
            ha,
            state,
            self.notifier,
            lambda: self.states,
            self._set_states,
        )
        self.ha.add_event_handler(self.handle_event)
        self.callback_server = CallbackServer(
            config.service_host,
            config.service_port,
            config.callback_token,
            self.watering.handle_water_request,
        )
        self._plant_by_id = {plant.id: plant for plant in plants}

    def _set_states(self, states: dict[str, EntityState]) -> None:
        self.states = states

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
                await self.watering.handle_water_request(plant_id, None)
            elif isinstance(action, str) and action.startswith(SNOOZE_ACTION_PREFIX):
                plant_id = action.removeprefix(SNOOZE_ACTION_PREFIX)
                await self.handle_snooze_request(plant_id)

    async def evaluate_and_notify(self, now: datetime | None = None) -> list[PlantStatus]:
        now = now or datetime.now(UTC)
        update_conditions(self.plants, self.states, self.state, now)
        active_records = active_condition_records(self.state)
        watering_allowed = self._watering_allowed_by_plant(active_records, now)
        statuses = plant_statuses_from_conditions(self.plants, active_records, watering_allowed)
        planning = NotificationPlanner(
            self.plants,
            self.state,
            self.config.alert_repeat_hours,
        ).build(
            statuses=statuses,
            active_records=active_records,
            watering_allowed=watering_allowed,
            now=now,
        )
        for plan in planning.plans:
            message = await rewrite_notification_text(
                self.config,
                plan.plant,
                plan.status,
                urgent_message(plan.status),
            )
            await self.notifier.send_urgent(plan.plant, plan.status, message=message)
            if self.config.dry_run:
                LOGGER.info("DRY_RUN alert not recorded as sent for %s", plan.plant.id)
            else:
                mark_notified(plan.due_records, now)
                self.state.last_alert_label[plan.plant.id] = _alert_key(plan.status)
                self.state.last_alert_sent_at[plan.plant.id] = now
        for plant_id in planning.clear_alert_plant_ids:
            self.state.last_alert_label.pop(plant_id, None)
            self.state.last_alert_sent_at.pop(plant_id, None)
            self.state.alert_snoozed_until.pop(plant_id, None)
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
                update_conditions(self.plants, self.states, self.state, now)
                statuses = plant_statuses_from_conditions(
                    self.plants,
                    active_condition_records(self.state),
                    self._watering_allowed_by_plant(active_condition_records(self.state), now),
                )
                await self.notifier.send_weekly_digest(self.plants, statuses)
                self.state.last_weekly_key = weekly_key
                self.state.save(self.config.state_path)
            await asyncio.sleep(300)

    async def _scheduled_job_loop(self) -> None:
        while True:
            await self.watering.run_due_scheduled_jobs()
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

    def _log_startup_health(self, statuses: list[PlantStatus]) -> None:
        counts = _status_counts(statuses)
        LOGGER.info(
            "Startup plant health: green=%s orange=%s red=%s next_alert=%s",
            counts[Severity.GREEN],
            counts[Severity.ORANGE],
            counts[Severity.RED],
            _next_alert_summary(
                self.plants,
                statuses,
                self.state,
                self.config.alert_repeat_hours,
            ),
        )

    def _watering_allowed_by_plant(
        self,
        records,
        now: datetime,
    ) -> dict[str, bool]:
        allowed: dict[str, bool] = {}
        for plant in self.plants:
            if not any(
                record.plant_id == plant.id and record.watering_candidate
                for record in records
            ):
                allowed[plant.id] = False
                continue
            allowed[plant.id] = watering_decision(
                plant,
                self.states,
                self.state.last_watered_at.get(plant.id),
                now=now,
            ).allowed
        return allowed


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
    if state.condition_records:
        plant_ids = {plant.id for plant in plants}
        for record in active_condition_records(state):
            if record.plant_id not in plant_ids or not record.phone_alert:
                continue
            if record.suppressed_until and now < record.suppressed_until.astimezone(UTC):
                next_times.append(record.suppressed_until.astimezone(UTC))
                continue
            snoozed_until = state.alert_snoozed_until.get(record.plant_id)
            if snoozed_until and now < snoozed_until.astimezone(UTC):
                next_times.append(snoozed_until.astimezone(UTC))
                continue
            if record.last_notified_at is None:
                next_times.append(now)
                continue
            next_times.append(
                record.last_notified_at.astimezone(UTC) + timedelta(hours=repeat_hours)
            )
        return _format_next_alert(next_times, now)

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

    return _format_next_alert(next_times, now)


def _format_next_alert(next_times: list[datetime], now: datetime) -> str:
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

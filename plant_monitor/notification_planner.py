from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from plant_monitor.condition_engine import due_phone_conditions, plant_statuses_from_conditions
from plant_monitor.models import PlantConfig, PlantStatus, Severity
from plant_monitor.runtime_state import ConditionRecord, RuntimeState


@dataclass(frozen=True)
class NotificationPlan:
    plant: PlantConfig
    status: PlantStatus
    due_records: list[ConditionRecord]


@dataclass(frozen=True)
class NotificationPlanningResult:
    plans: list[NotificationPlan]
    clear_alert_plant_ids: list[str]


class NotificationPlanner:
    def __init__(
        self,
        plants: list[PlantConfig],
        state: RuntimeState,
        repeat_hours: int,
    ) -> None:
        self.plants = plants
        self.state = state
        self.repeat_hours = repeat_hours

    def build(
        self,
        *,
        statuses: list[PlantStatus],
        active_records: list[ConditionRecord],
        watering_allowed: dict[str, bool],
        now: datetime,
    ) -> NotificationPlanningResult:
        now = _aware(now)
        plans: list[NotificationPlan] = []
        clear_alert_plant_ids: list[str] = []

        for plant, status in zip(self.plants, statuses, strict=True):
            due_records = due_phone_conditions(
                self.state,
                plant.id,
                self.repeat_hours,
                now,
            )
            if self._is_snoozed(plant.id, now):
                continue
            if due_records:
                plans.append(
                    NotificationPlan(
                        plant=plant,
                        status=_status_for_alert(
                            plant,
                            active_records,
                            due_records,
                            watering_allowed,
                        ),
                        due_records=due_records,
                    )
                )
            elif status.label == Severity.GREEN and not status.watering_recommended:
                clear_alert_plant_ids.append(plant.id)

        return NotificationPlanningResult(plans, clear_alert_plant_ids)

    def _is_snoozed(self, plant_id: str, now: datetime) -> bool:
        until = self.state.alert_snoozed_until.get(plant_id)
        if until is None:
            return False
        if now < until.astimezone(UTC):
            return True
        self.state.alert_snoozed_until.pop(plant_id, None)
        return False


def _status_for_alert(
    plant: PlantConfig,
    active_records: list[ConditionRecord],
    due_records: list[ConditionRecord],
    watering_allowed: dict[str, bool],
) -> PlantStatus:
    alert_records = [
        record
        for record in active_records
        if record.plant_id == plant.id
        and (
            record in due_records
            or (record.sensor == "humidity" and record.severity == "red")
        )
    ]
    return plant_statuses_from_conditions([plant], alert_records, watering_allowed)[0]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

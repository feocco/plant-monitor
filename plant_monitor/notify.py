from __future__ import annotations

from plant_monitor.ha import HomeAssistantClient
from plant_monitor.models import PlantConfig, PlantStatus, Severity
from plant_monitor.rules import overall_label

WATER_ACTION_PREFIX = "PLANT_WATER::"
SNOOZE_ACTION_PREFIX = "PLANT_SNOOZE::"


class Notifier:
    def __init__(self, ha: HomeAssistantClient, notify_service: str, dashboard_url: str) -> None:
        self.ha = ha
        self.dashboard_url = dashboard_url
        self.domain, self.service = _split_service(notify_service)

    async def send_urgent(self, plant: PlantConfig, status: PlantStatus) -> None:
        title = f"{status.label.label.upper()}: {plant.location} {plant.name}"
        actions = self._dashboard_actions()
        actions.append(
            {
                "action": f"{SNOOZE_ACTION_PREFIX}{plant.id}",
                "title": "Delay 24h",
            }
        )
        if status.watering_recommended and plant.entities.pump:
            actions.append(
                {
                    "action": f"{WATER_ACTION_PREFIX}{plant.id}",
                    "title": f"Water {plant.watering.max_seconds}s",
                }
            )
        await self._send(
            title=title,
            message=_urgent_message(status),
            data={
                "tag": f"plant-monitor-{plant.id}",
                "group": "plant-monitor",
                "actions": actions,
                **self._open_data(),
            },
        )

    async def send_weekly_digest(self, plants: list[PlantConfig], statuses: list[PlantStatus]) -> None:
        label = overall_label(statuses)
        await self._send(
            title=f"Plant status: {label.label.upper()}",
            message=format_digest(plants, statuses),
            data={
                "tag": "plant-monitor-weekly",
                "group": "plant-monitor",
                "actions": self._dashboard_actions(),
                **self._open_data(),
            },
        )

    async def send_watering_result(self, plant: PlantConfig, message: str) -> None:
        await self._send(
            title=f"Watering: {plant.location} {plant.name}",
            message=message,
            data={"tag": f"plant-monitor-water-{plant.id}", "group": "plant-monitor"},
        )

    async def send_alert_snoozed(self, plant: PlantConfig, message: str) -> None:
        await self._send(
            title=f"Plant alerts delayed: {plant.location} {plant.name}",
            message=message,
            data={
                "tag": f"plant-monitor-snooze-{plant.id}",
                "group": "plant-monitor",
                **self._open_data(),
            },
        )

    async def _send(self, title: str, message: str, data: dict) -> None:
        await self.ha.call_service(
            self.domain,
            self.service,
            {"title": title, "message": message, "data": data},
        )

    def _dashboard_actions(self) -> list[dict]:
        if not self.dashboard_url:
            return []
        return [{"action": "URI", "title": "Open Plants", "uri": self.dashboard_url}]

    def _open_data(self) -> dict:
        if not self.dashboard_url:
            return {}
        return {
            "url": self.dashboard_url,
            "clickAction": self.dashboard_url,
        }


def should_send_urgent(status: PlantStatus) -> bool:
    return status.label in {Severity.ORANGE, Severity.RED} or status.watering_recommended


def format_digest(plants: list[PlantConfig], statuses: list[PlantStatus]) -> str:
    counts = {
        Severity.RED: sum(1 for status in statuses if status.label == Severity.RED),
        Severity.ORANGE: sum(1 for status in statuses if status.label == Severity.ORANGE),
        Severity.GREEN: sum(1 for status in statuses if status.label == Severity.GREEN),
    }
    lines = [
        f"RED {counts[Severity.RED]} | ORANGE {counts[Severity.ORANGE]} | GREEN {counts[Severity.GREEN]}"
    ]
    plant_by_id = {plant.id: plant for plant in plants}
    for status in sorted(statuses, key=lambda item: item.label, reverse=True):
        if status.label == Severity.GREEN:
            continue
        plant = plant_by_id[status.plant_id]
        lines.append("")
        lines.append(f"{status.label.label.upper()} {plant.location} {plant.name}")
        for issue in _display_issues(status)[:3]:
            lines.append(f"- {issue.message.rstrip('.')}")
        if status.watering_recommended:
            lines.append("- watering recommended")
    if len(lines) == 1:
        lines.append("")
        lines.append("All monitored plants are green.")
    return "\n".join(lines)


def _urgent_message(status: PlantStatus) -> str:
    lines = [issue.message.rstrip(".") for issue in _display_issues(status)[:4]]
    if status.watering_recommended:
        lines.append("watering recommended")
    return "\n".join(f"- {line}" for line in lines) if lines else status.summary


def _display_issues(status: PlantStatus) -> list:
    issues = [issue for issue in status.issues if issue.sensor != "plant"]
    return issues or list(status.issues)


def _split_service(value: str) -> tuple[str, str]:
    if "." not in value:
        raise ValueError("HA_NOTIFY_SERVICE must look like notify.mobile_app_phone")
    domain, service = value.split(".", 1)
    return domain, service

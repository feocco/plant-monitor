from __future__ import annotations

import asyncio
from typing import Any

from homelab import notify_joe

from plant_monitor.models import PlantConfig, PlantStatus, Severity
from plant_monitor.rules import overall_label

WATER_ACTION_PREFIX = "PLANT_WATER::"
SNOOZE_ACTION_PREFIX = "PLANT_SNOOZE::"


class Notifier:
    def __init__(
        self,
        dashboard_url: str,
        service_url: str | None = None,
        *,
        token: str | None = None,
        timeout: float = 10,
    ) -> None:
        self.dashboard_url = dashboard_url
        self.service_url = service_url
        self.token = token
        self.timeout = timeout

    async def send_urgent(self, plant: PlantConfig, status: PlantStatus) -> None:
        title = f"{status.label.label.upper()}: {plant.location} {plant.name}"
        buttons = self._dashboard_buttons()
        buttons.append(
            {
                "title": "Delay 24h",
                "action": f"{SNOOZE_ACTION_PREFIX}{plant.id}",
            }
        )
        if status.watering_recommended and plant.entities.pump:
            buttons.append(
                {
                    "title": f"Water {plant.watering.max_seconds}s",
                    "action": f"{WATER_ACTION_PREFIX}{plant.id}",
                }
            )
        await self._send(
            title=title,
            message=_urgent_message(status),
            tag=f"plant-monitor-{plant.id}",
            group="plant-monitor",
            url=self._dashboard_url(),
            buttons=buttons,
        )

    async def send_weekly_digest(self, plants: list[PlantConfig], statuses: list[PlantStatus]) -> None:
        label = overall_label(statuses)
        await self._send(
            title=f"Plant status: {label.label.upper()}",
            message=format_digest(plants, statuses),
            tag="plant-monitor-weekly",
            group="plant-monitor",
            url=self._dashboard_url(),
            buttons=self._dashboard_buttons(),
        )

    async def send_watering_result(self, plant: PlantConfig, message: str) -> None:
        await self._send(
            title=f"Watering: {plant.location} {plant.name}",
            message=message,
            tag=f"plant-monitor-water-{plant.id}",
            group="plant-monitor",
        )

    async def send_watering_lookback(self, plant: PlantConfig, message: str) -> None:
        await self._send(
            title=f"Watering follow-up: {plant.location} {plant.name}",
            message=message,
            tag=f"plant-monitor-water-lookback-{plant.id}",
            group="plant-monitor",
        )

    async def send_alert_snoozed(self, plant: PlantConfig, message: str) -> None:
        await self._send(
            title=f"Plant alerts delayed: {plant.location} {plant.name}",
            message=message,
            tag=f"plant-monitor-snooze-{plant.id}",
            group="plant-monitor",
            url=self._dashboard_url(),
        )

    async def _send(
        self,
        title: str,
        message: str,
        *,
        tag: str,
        group: str,
        url: str | None = None,
        buttons: list[dict[str, Any]] | None = None,
    ) -> None:
        await asyncio.to_thread(
            notify_joe,
            title,
            message,
            tag=tag,
            group=group,
            url=url,
            buttons=buttons or None,
            service_url=self.service_url,
            token=self.token,
            timeout=self.timeout,
        )

    def _dashboard_buttons(self) -> list[dict[str, str]]:
        if not self.dashboard_url:
            return []
        return [{"title": "Open Plants", "action": "URI", "uri": self.dashboard_url}]

    def _dashboard_url(self) -> str | None:
        return self.dashboard_url or None


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

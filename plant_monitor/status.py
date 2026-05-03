from __future__ import annotations

import argparse
import asyncio

from rich.console import Console
from rich.table import Table

from plant_monitor.config import load_plants, load_service_config
from plant_monitor.ha import HomeAssistantClient
from plant_monitor.logging_config import setup_logging
from plant_monitor.models import PlantConfig, PlantStatus, Severity
from plant_monitor.notify import Notifier
from plant_monitor.rules import evaluate_plant

SEVERITY_STYLES = {
    Severity.GREEN: "bold green",
    Severity.ORANGE: "bold yellow",
    Severity.RED: "bold red",
}


async def run(send_notification: bool) -> None:
    config = load_service_config()
    setup_logging(config.log_level)
    plants = load_plants(config.config_path)
    ha = HomeAssistantClient(config)
    await ha.connect()
    try:
        states = await ha.get_states()
        statuses = [evaluate_plant(plant, states) for plant in plants]
        _print_table(plants, statuses)
        if send_notification:
            notifier = Notifier(
                config.plants_dashboard_url,
                service_url=config.homelab_functions_url,
                token=config.homelab_functions_token,
            )
            await notifier.send_weekly_digest(plants, statuses)
    finally:
        await ha.close()


def _print_table(plants: list[PlantConfig], statuses: list[PlantStatus]) -> None:
    table = Table(title="Plant Monitor Status", show_lines=True)
    table.add_column("Status")
    table.add_column("Plant")
    table.add_column("Location")
    table.add_column("Why")
    table.add_column("Water")

    for plant, status in zip(plants, statuses, strict=True):
        display_issues = [issue for issue in status.issues if issue.sensor != "plant"] or list(status.issues)
        issues = "\n".join(issue.message.rstrip(".") for issue in display_issues) or "No issues"
        water = "recommended" if status.watering_recommended else ""
        table.add_row(
            status.label.label.upper(),
            plant.name,
            plant.location,
            issues,
            water,
            style=SEVERITY_STYLES[status.label],
        )

    Console().print(table)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print current plant status.")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send the current status as a homelab-functions notification digest.",
    )
    args = parser.parse_args()
    asyncio.run(run(send_notification=args.notify))


if __name__ == "__main__":
    main()

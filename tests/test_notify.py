from __future__ import annotations

from unittest.mock import patch

from plant_monitor.models import (
    EntityMap,
    Issue,
    PlantConfig,
    PlantStatus,
    Severity,
    WateringConfig,
)
from plant_monitor.notify import Notifier, format_digest


async def test_send_urgent_maps_actions_to_homelab_buttons() -> None:
    plant = _plant(
        "pothos",
        "Golden Pothos",
        "Office shelf",
        pump="switch.office_shelf_pump",
        max_seconds=9,
    )
    status = PlantStatus(
        plant_id=plant.id,
        label=Severity.RED,
        issues=(Issue(Severity.RED, "moisture", "moisture is low at 12%."),),
        watering_recommended=True,
        summary="",
    )
    notifier = Notifier(
        "/lovelace/plants",
        service_url="http://homelab-functions:8091",
        token="secret",
        timeout=2,
    )

    with patch("plant_monitor.notify.notify_joe") as notify_joe:
        await notifier.send_urgent(plant, status)

    notify_joe.assert_called_once_with(
        "RED: Office shelf Golden Pothos",
        "- moisture is low at 12%\n- watering recommended",
        tag="plant-monitor-pothos",
        group="plant-monitor",
        url="/lovelace/plants",
        buttons=[
            {"title": "Open Plants", "action": "URI", "uri": "/lovelace/plants"},
            {"title": "Delay 24h", "action": "PLANT_SNOOZE::pothos"},
            {"title": "Water 9s", "action": "PLANT_WATER::pothos"},
        ],
        service_url="http://homelab-functions:8091",
        token="secret",
        timeout=2,
    )


async def test_watering_result_preserves_tag_and_group_without_dashboard_actions() -> None:
    plant = _plant("pothos", "Golden Pothos", "Office shelf")
    notifier = Notifier("/lovelace/plants")

    with patch("plant_monitor.notify.notify_joe") as notify_joe:
        await notifier.send_watering_result(plant, "Watered for 9 seconds.")

    notify_joe.assert_called_once_with(
        "Watering: Office shelf Golden Pothos",
        "Watered for 9 seconds.",
        tag="plant-monitor-water-pothos",
        group="plant-monitor",
        url=None,
        buttons=None,
        service_url=None,
        token=None,
        timeout=10,
    )


def test_format_digest_is_compact_and_groups_non_green_statuses() -> None:
    plants = [_plant("fern", "Boston Fern", "Hanging"), _plant("pothos", "Golden Pothos", "Office")]
    statuses = [
        PlantStatus(
            plant_id="fern",
            label=Severity.RED,
            issues=(Issue(Severity.RED, "battery", "battery is critically low at 14.5%."),),
            watering_recommended=False,
            summary="",
        ),
        PlantStatus(
            plant_id="pothos",
            label=Severity.GREEN,
            issues=(),
            watering_recommended=False,
            summary="",
        ),
    ]

    digest = format_digest(plants, statuses)

    assert "RED 1 | ORANGE 0 | GREEN 1" in digest
    assert "RED Hanging Boston Fern" in digest
    assert "- battery is critically low at 14.5%" in digest
    assert "Office Golden Pothos" not in digest


def test_format_digest_hides_generic_plant_problem_when_specific_issues_exist() -> None:
    plants = [_plant("pothos", "Golden Pothos", "Office Hanging")]
    statuses = [
        PlantStatus(
            plant_id="pothos",
            label=Severity.RED,
            issues=(
                Issue(Severity.ORANGE, "plant", "Home Assistant plant entity reports a problem."),
                Issue(Severity.RED, "moisture", "moisture is low at 14.5%."),
            ),
            watering_recommended=False,
            summary="",
        )
    ]

    digest = format_digest(plants, statuses)

    assert "Home Assistant plant entity reports a problem" not in digest
    assert "moisture is low at 14.5%" in digest


def _plant(
    plant_id: str,
    name: str,
    location: str,
    *,
    pump: str | None = None,
    max_seconds: int = 8,
) -> PlantConfig:
    return PlantConfig(
        id=plant_id,
        name=name,
        location=location,
        species="default",
        plant_entity=f"plant.{plant_id}",
        entities=EntityMap(pump=pump),
        watering=WateringConfig(switch=pump, max_seconds=max_seconds),
    )

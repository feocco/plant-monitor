from __future__ import annotations

from datetime import UTC, datetime

from plant_monitor.models import EntityMap, Issue, PlantConfig, PlantStatus, Severity
from plant_monitor.notify import format_digest


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


def _plant(plant_id: str, name: str, location: str) -> PlantConfig:
    return PlantConfig(
        id=plant_id,
        name=name,
        location=location,
        species="default",
        plant_entity=f"plant.{plant_id}",
        entities=EntityMap(),
    )

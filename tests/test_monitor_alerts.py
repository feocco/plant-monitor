from __future__ import annotations

from plant_monitor.monitor import _alert_key
from plant_monitor.models import Issue, PlantStatus, Severity


def test_alert_key_ignores_numeric_value_drift_for_same_issue() -> None:
    first = PlantStatus(
        plant_id="pothos",
        label=Severity.RED,
        issues=(Issue(Severity.RED, "moisture", "moisture is low at 14.5%."),),
        watering_recommended=False,
        summary="",
    )
    second = PlantStatus(
        plant_id="pothos",
        label=Severity.RED,
        issues=(Issue(Severity.RED, "moisture", "moisture is low at 13.9%."),),
        watering_recommended=False,
        summary="",
    )

    assert _alert_key(first) == _alert_key(second)

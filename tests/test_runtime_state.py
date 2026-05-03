from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plant_monitor.runtime_state import RuntimeState, ScheduledJob

NOW = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)


def test_runtime_state_roundtrips_scheduled_jobs(tmp_path) -> None:
    path = tmp_path / "state.json"
    job = ScheduledJob(
        id="watering_lookback:office_pothos:2026-05-02T12:00:00+00:00:3600",
        kind="watering_lookback",
        plant_id="office_pothos",
        due_at=NOW + timedelta(hours=1),
        payload={
            "watered_at": NOW.isoformat(),
            "delay_seconds": 3600,
            "baseline": [
                {
                    "sensor": "moisture",
                    "entity_id": "sensor.office_pothos_moisture",
                    "value": 12.0,
                    "last_updated": NOW.isoformat(),
                }
            ],
        },
    )
    state = RuntimeState(
        last_watered_at={"office_pothos": NOW},
        scheduled_jobs=[job],
    )

    state.save(path)
    loaded = RuntimeState.load(path)

    assert loaded.last_watered_at == {"office_pothos": NOW}
    assert loaded.scheduled_jobs_schema_version == 1
    assert loaded.scheduled_jobs == [job]

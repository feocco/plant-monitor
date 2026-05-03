from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEDULED_JOBS_SCHEMA_VERSION = 1


@dataclass
class ScheduledJob:
    id: str
    kind: str
    plant_id: str
    due_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeState:
    last_watered_at: dict[str, datetime] = field(default_factory=dict)
    last_alert_label: dict[str, str] = field(default_factory=dict)
    last_alert_sent_at: dict[str, datetime] = field(default_factory=dict)
    alert_snoozed_until: dict[str, datetime] = field(default_factory=dict)
    last_weekly_key: str | None = None
    last_dry_run: bool | None = None
    scheduled_jobs_schema_version: int = SCHEDULED_JOBS_SCHEMA_VERSION
    scheduled_jobs: list[ScheduledJob] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "RuntimeState":
        state_path = Path(path)
        if not state_path.exists():
            return cls()
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        return cls(
            last_watered_at={
                plant_id: datetime.fromisoformat(value)
                for plant_id, value in (raw.get("last_watered_at") or {}).items()
            },
            last_alert_label=dict(raw.get("last_alert_label") or {}),
            last_alert_sent_at={
                plant_id: datetime.fromisoformat(value)
                for plant_id, value in (raw.get("last_alert_sent_at") or {}).items()
            },
            alert_snoozed_until={
                plant_id: datetime.fromisoformat(value)
                for plant_id, value in (raw.get("alert_snoozed_until") or {}).items()
            },
            last_weekly_key=raw.get("last_weekly_key"),
            last_dry_run=raw.get("last_dry_run"),
            scheduled_jobs_schema_version=int(
                raw.get("scheduled_jobs_schema_version", SCHEDULED_JOBS_SCHEMA_VERSION)
            ),
            scheduled_jobs=[
                ScheduledJob(
                    id=str(item["id"]),
                    kind=str(item["kind"]),
                    plant_id=str(item["plant_id"]),
                    due_at=datetime.fromisoformat(item["due_at"]),
                    payload=dict(item.get("payload") or {}),
                )
                for item in (raw.get("scheduled_jobs") or [])
            ],
        )

    def save(self, path: str | Path) -> None:
        state_path = Path(path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_watered_at": {
                plant_id: value.isoformat()
                for plant_id, value in self.last_watered_at.items()
            },
            "last_alert_label": self.last_alert_label,
            "last_alert_sent_at": {
                plant_id: value.isoformat()
                for plant_id, value in self.last_alert_sent_at.items()
            },
            "alert_snoozed_until": {
                plant_id: value.isoformat()
                for plant_id, value in self.alert_snoozed_until.items()
            },
            "last_weekly_key": self.last_weekly_key,
            "last_dry_run": self.last_dry_run,
            "scheduled_jobs_schema_version": self.scheduled_jobs_schema_version,
            "scheduled_jobs": [
                {
                    "id": job.id,
                    "kind": job.kind,
                    "plant_id": job.plant_id,
                    "due_at": job.due_at.isoformat(),
                    "payload": job.payload,
                }
                for job in self.scheduled_jobs
            ],
        }
        tmp_path = state_path.with_name(f".{state_path.name}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(state_path)

    def upsert_scheduled_job(self, job: ScheduledJob) -> None:
        self.scheduled_jobs = [
            existing for existing in self.scheduled_jobs if existing.id != job.id
        ]
        self.scheduled_jobs.append(job)

    def remove_scheduled_job(self, job_id: str) -> None:
        self.scheduled_jobs = [job for job in self.scheduled_jobs if job.id != job_id]

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
class ConditionRecord:
    key: str
    plant_id: str
    kind: str
    sensor: str
    severity: str
    message: str
    first_seen_at: datetime
    last_seen_at: datetime
    last_value: float | None = None
    active_since: datetime | None = None
    last_notified_at: datetime | None = None
    suppressed_until: datetime | None = None
    resolved_at: datetime | None = None
    phone_alert: bool = False
    watering_candidate: bool = False


@dataclass
class SensorSample:
    timestamp: datetime
    plant_id: str
    sensor: str
    entity_id: str
    value: float


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
    condition_records: dict[str, ConditionRecord] = field(default_factory=dict)
    samples: list[SensorSample] = field(default_factory=list)

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
            condition_records={
                key: _condition_record_from_payload(value)
                for key, value in (raw.get("condition_records") or {}).items()
            },
            samples=[
                SensorSample(
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                    plant_id=str(item["plant_id"]),
                    sensor=str(item["sensor"]),
                    entity_id=str(item["entity_id"]),
                    value=float(item["value"]),
                )
                for item in (raw.get("samples") or [])
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
            "condition_records": {
                key: _condition_record_payload(record)
                for key, record in self.condition_records.items()
            },
            "samples": [
                {
                    "timestamp": sample.timestamp.isoformat(),
                    "plant_id": sample.plant_id,
                    "sensor": sample.sensor,
                    "entity_id": sample.entity_id,
                    "value": sample.value,
                }
                for sample in self.samples
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


def _condition_record_from_payload(payload: dict[str, Any]) -> ConditionRecord:
    return ConditionRecord(
        key=str(payload["key"]),
        plant_id=str(payload["plant_id"]),
        kind=str(payload["kind"]),
        sensor=str(payload["sensor"]),
        severity=str(payload["severity"]),
        message=str(payload["message"]),
        first_seen_at=datetime.fromisoformat(payload["first_seen_at"]),
        last_seen_at=datetime.fromisoformat(payload["last_seen_at"]),
        last_value=_float_or_none(payload.get("last_value")),
        active_since=_datetime_or_none(payload.get("active_since")),
        last_notified_at=_datetime_or_none(payload.get("last_notified_at")),
        suppressed_until=_datetime_or_none(payload.get("suppressed_until")),
        resolved_at=_datetime_or_none(payload.get("resolved_at")),
        phone_alert=bool(payload.get("phone_alert", False)),
        watering_candidate=bool(payload.get("watering_candidate", False)),
    )


def _condition_record_payload(record: ConditionRecord) -> dict[str, Any]:
    return {
        "key": record.key,
        "plant_id": record.plant_id,
        "kind": record.kind,
        "sensor": record.sensor,
        "severity": record.severity,
        "message": record.message,
        "first_seen_at": record.first_seen_at.isoformat(),
        "last_seen_at": record.last_seen_at.isoformat(),
        "last_value": record.last_value,
        "active_since": record.active_since.isoformat() if record.active_since else None,
        "last_notified_at": record.last_notified_at.isoformat()
        if record.last_notified_at
        else None,
        "suppressed_until": record.suppressed_until.isoformat()
        if record.suppressed_until
        else None,
        "resolved_at": record.resolved_at.isoformat() if record.resolved_at else None,
        "phone_alert": record.phone_alert,
        "watering_candidate": record.watering_candidate,
    }


def _datetime_or_none(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def _float_or_none(value: object) -> float | None:
    return None if value is None else float(value)

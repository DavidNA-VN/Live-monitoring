from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from uuid import NAMESPACE_URL, uuid5


ALERT_SCHEMA_VERSION = "1.0"


class AlertCategory(str, Enum):
    CONTENT = "content"
    RUNTIME = "runtime"


@dataclass(frozen=True)
class AlertEnvelope:
    alert_id: str
    event_id: str
    category: AlertCategory
    event_type: str
    state: str
    stream_id: str
    occurred_at: datetime
    emitted_at: datetime
    reason: str
    check: str | None = None
    variant_id: str | None = None
    event_started_at: datetime | None = None
    event_ended_at: datetime | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    schema_version: str = ALERT_SCHEMA_VERSION

    def to_redis_fields(self) -> dict[str, str]:
        fields = {
            "schema_version": self.schema_version,
            "alert_id": self.alert_id,
            "event_id": self.event_id,
            "category": self.category.value,
            "type": self.event_type,
            "state": self.state,
            "stream_id": self.stream_id,
            "occurred_at": self._time(self.occurred_at),
            "emitted_at": self._time(self.emitted_at),
            "reason": self.reason,
            "payload": json.dumps(
                self.attributes,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
        for key, value in self.attributes.items():
            fields.setdefault(key, value)
        if self.check is not None:
            fields["check"] = self.check
        if self.variant_id is not None:
            fields["variant_id"] = self.variant_id
        if self.event_started_at is not None:
            fields["event_started_at"] = self._time(
                self.event_started_at
            )
        if self.event_ended_at is not None:
            fields["event_ended_at"] = self._time(self.event_ended_at)
        return fields

    @classmethod
    def from_redis_fields(cls, fields: dict[str, str]) -> "AlertEnvelope":
        required = (
            "schema_version",
            "alert_id",
            "event_id",
            "category",
            "type",
            "state",
            "stream_id",
            "occurred_at",
            "emitted_at",
            "reason",
        )
        missing = [name for name in required if not fields.get(name)]
        if missing:
            raise ValueError(
                f"Alert envelope missing fields: {', '.join(missing)}"
            )
        if fields["schema_version"] != ALERT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported alert schema: {fields['schema_version']}"
            )
        try:
            attributes = json.loads(fields.get("payload", "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError("Alert payload is not valid JSON") from exc
        if not isinstance(attributes, dict):
            raise ValueError("Alert payload must be an object")
        return cls(
            schema_version=fields["schema_version"],
            alert_id=fields["alert_id"],
            event_id=fields["event_id"],
            category=AlertCategory(fields["category"]),
            event_type=fields["type"],
            state=fields["state"],
            stream_id=fields["stream_id"],
            occurred_at=datetime.fromisoformat(fields["occurred_at"]),
            emitted_at=datetime.fromisoformat(fields["emitted_at"]),
            reason=fields["reason"],
            check=fields.get("check"),
            variant_id=fields.get("variant_id"),
            event_started_at=cls._optional_time(
                fields.get("event_started_at")
            ),
            event_ended_at=cls._optional_time(
                fields.get("event_ended_at")
            ),
            attributes={str(key): str(value) for key, value in attributes.items()},
        )

    @staticmethod
    def _time(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _optional_time(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None


def deterministic_alert_id(
    *,
    stream_id: str,
    event_id: str,
    state: str,
    reason: str,
    revision: str,
) -> str:
    value = ":".join(
        (stream_id, event_id, state, reason, revision)
    )
    return str(uuid5(NAMESPACE_URL, value))

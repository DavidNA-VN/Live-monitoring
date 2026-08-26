from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any

from core.monitoring_control import MonitoringAction


COMMAND_SCHEMA_VERSION = "1.0"


class MonitoringCommandError(ValueError):
    pass


class MonitoringCommandResultStatus(str, Enum):
    APPLIED = "APPLIED"
    NOOP = "NOOP"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MonitoringCommandError(f"{field} must be an ISO-8601 datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MonitoringCommandError(
            f"{field} must be an ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None:
        raise MonitoringCommandError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class MonitoringCommand:
    command_id: str
    action: MonitoringAction
    stream_id: str
    requested_at: datetime
    config: dict[str, Any] | None = None
    schema_version: str = COMMAND_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, data: object) -> "MonitoringCommand":
        if not isinstance(data, dict):
            raise MonitoringCommandError("command payload must be an object")
        allowed = {
            "schema_version",
            "command_id",
            "command_type",
            "stream_id",
            "requested_at",
            "config",
        }
        unknown = set(data) - allowed
        if unknown:
            raise MonitoringCommandError(
                f"unknown command fields: {', '.join(sorted(unknown))}"
            )
        if data.get("schema_version") != COMMAND_SCHEMA_VERSION:
            raise MonitoringCommandError("unsupported command schema_version")
        command_id = data.get("command_id")
        stream_id = data.get("stream_id")
        if not isinstance(command_id, str) or not command_id:
            raise MonitoringCommandError("command_id must not be empty")
        if not isinstance(stream_id, str) or not stream_id.strip():
            raise MonitoringCommandError("stream_id must not be empty")
        if len(stream_id) > 128:
            raise MonitoringCommandError("stream_id must not exceed 128 characters")
        try:
            action = MonitoringAction(data.get("command_type"))
        except (TypeError, ValueError) as exc:
            raise MonitoringCommandError("unsupported command_type") from exc
        config = data.get("config")
        if action in (MonitoringAction.START, MonitoringAction.UPDATE_CONFIG):
            if not isinstance(config, dict):
                raise MonitoringCommandError(
                    f"config is required for {action.value}"
                )
        elif config is not None and not isinstance(config, dict):
            raise MonitoringCommandError("config must be an object")
        return cls(
            command_id=command_id,
            action=action,
            stream_id=stream_id.strip(),
            requested_at=_parse_datetime(data.get("requested_at"), "requested_at"),
            config=dict(config) if isinstance(config, dict) else None,
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> "MonitoringCommand":
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            data = json.loads(payload)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MonitoringCommandError("command payload must be valid JSON") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "command_type": self.action.value,
            "stream_id": self.stream_id,
            "requested_at": self.requested_at.astimezone(timezone.utc).isoformat(),
        }
        if self.config is not None:
            payload["config"] = self.config
        return payload


@dataclass(frozen=True)
class MonitoringCommandResult:
    command_id: str
    action: MonitoringAction
    stream_id: str
    status: MonitoringCommandResultStatus
    changed: bool
    processed_at: datetime
    error_code: str | None = None
    error: str | None = None
    schema_version: str = COMMAND_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "command_type": self.action.value,
            "stream_id": self.stream_id,
            "status": self.status.value,
            "changed": self.changed,
            "processed_at": self.processed_at.astimezone(timezone.utc).isoformat(),
            "error_code": self.error_code,
            "error": self.error,
        }

    def to_redis_fields(self) -> dict[str, str]:
        return {"payload": json.dumps(self.to_dict(), separators=(",", ":"))}

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from models.runtime_status import (
    RUNTIME_STATUS_SCHEMA_VERSION,
    WORKER_ID_REGEX,
    RuntimeStatus,
)

RUNTIME_STATUS_UPDATE_SCHEMA_VERSION = RUNTIME_STATUS_SCHEMA_VERSION


class RuntimeStatusUpdateType(str, Enum):
    SNAPSHOT = "SNAPSHOT"
    REMOVED = "REMOVED"


@dataclass(frozen=True)
class RuntimeStatusUpdate:
    update_id: str
    update_type: RuntimeStatusUpdateType
    stream_id: str
    worker_id: str
    observed_at: datetime
    status: RuntimeStatus | None = None
    schema_version: str = RUNTIME_STATUS_UPDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.update_id, str) or not self.update_id.strip():
            raise ValueError("update_id must be a non-empty string")
        if not isinstance(self.stream_id, str) or not self.stream_id.strip() or len(self.stream_id) > 128:
            raise ValueError("stream_id must be a non-empty string of length <= 128")
        if not isinstance(self.worker_id, str) or not WORKER_ID_REGEX.match(self.worker_id):
            raise ValueError(f"Invalid worker_id '{self.worker_id}'")
        if not isinstance(self.observed_at, datetime):
            raise ValueError("observed_at must be a datetime")
        if self.observed_at.tzinfo is None:
            object.__setattr__(self, "observed_at", self.observed_at.replace(tzinfo=timezone.utc))
        else:
            object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))

        if self.update_type == RuntimeStatusUpdateType.SNAPSHOT:
            if self.status is None or not isinstance(self.status, RuntimeStatus):
                raise ValueError("SNAPSHOT update requires a valid RuntimeStatus instance")
            if self.status.stream_id != self.stream_id:
                raise ValueError(
                    f"Status stream_id '{self.status.stream_id}' does not match update stream_id '{self.stream_id}'"
                )
            if self.status.worker_id != self.worker_id:
                raise ValueError(
                    f"Status worker_id '{self.status.worker_id}' does not match update worker_id '{self.worker_id}'"
                )
            if self.status.observed_at != self.observed_at:
                raise ValueError(
                    f"Status observed_at '{self.status.observed_at}' does not match update observed_at '{self.observed_at}'"
                )
        elif self.update_type == RuntimeStatusUpdateType.REMOVED:
            if self.status is not None:
                raise ValueError("REMOVED update must not contain a status payload")
        else:
            raise ValueError(f"Unsupported update_type '{self.update_type}'")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "update_id": self.update_id,
            "update_type": self.update_type.value,
            "stream_id": self.stream_id,
            "worker_id": self.worker_id,
            "observed_at": self.observed_at.isoformat(),
        }
        if self.update_type == RuntimeStatusUpdateType.SNAPSHOT and self.status is not None:
            payload["status"] = self.status.to_dict()
        return payload

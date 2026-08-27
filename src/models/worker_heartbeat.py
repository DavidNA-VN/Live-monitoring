from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from models.runtime_status import (
    RUNTIME_STATUS_SCHEMA_VERSION,
    WORKER_ID_REGEX,
    _format_datetime,
)

WORKER_HEARTBEAT_SCHEMA_VERSION = RUNTIME_STATUS_SCHEMA_VERSION


class WorkerState(str, Enum):
    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"


@dataclass(frozen=True)
class WorkerHeartbeat:
    worker_id: str
    state: WorkerState
    started_at: datetime
    last_seen_at: datetime
    command_consumer_ready: bool
    active_stream_count: int
    max_streams: int
    active_media_processes: int
    max_media_processes: int
    version: str
    schema_version: str = WORKER_HEARTBEAT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str) or not WORKER_ID_REGEX.match(self.worker_id):
            raise ValueError(f"Invalid worker_id '{self.worker_id}'")
        if not isinstance(self.state, WorkerState):
            raise ValueError(f"Invalid worker state '{self.state}'")
        if not isinstance(self.started_at, datetime):
            raise ValueError("started_at must be a datetime")
        if not isinstance(self.last_seen_at, datetime):
            raise ValueError("last_seen_at must be a datetime")

        # Normalize datetimes to UTC
        if self.started_at.tzinfo is None:
            object.__setattr__(self, "started_at", self.started_at.replace(tzinfo=timezone.utc))
        else:
            object.__setattr__(self, "started_at", self.started_at.astimezone(timezone.utc))

        if self.last_seen_at.tzinfo is None:
            object.__setattr__(self, "last_seen_at", self.last_seen_at.replace(tzinfo=timezone.utc))
        else:
            object.__setattr__(self, "last_seen_at", self.last_seen_at.astimezone(timezone.utc))

        if self.started_at > self.last_seen_at:
            raise ValueError("started_at must be <= last_seen_at")

        if self.active_stream_count < 0:
            raise ValueError("active_stream_count must be >= 0")
        if self.max_streams <= 0:
            raise ValueError("max_streams must be > 0")
        if self.active_stream_count > self.max_streams:
            raise ValueError("active_stream_count cannot exceed max_streams")

        if self.active_media_processes < 0:
            raise ValueError("active_media_processes must be >= 0")
        if self.max_media_processes <= 0:
            raise ValueError("max_media_processes must be > 0")
        if self.active_media_processes > self.max_media_processes:
            raise ValueError("active_media_processes cannot exceed max_media_processes")

        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "worker_id": self.worker_id,
            "state": self.state.value,
            "started_at": _format_datetime(self.started_at),
            "last_seen_at": _format_datetime(self.last_seen_at),
            "command_consumer_ready": self.command_consumer_ready,
            "active_stream_count": self.active_stream_count,
            "max_streams": self.max_streams,
            "active_media_processes": self.active_media_processes,
            "max_media_processes": self.max_media_processes,
            "version": self.version,
        }

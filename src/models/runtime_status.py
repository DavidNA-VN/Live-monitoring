from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from types import MappingProxyType
from typing import Any

RUNTIME_STATUS_SCHEMA_VERSION = "1.0"


class PublicStreamStatus(str, Enum):
    CREATED = "CREATED"
    STARTING = "STARTING"  # Reserved for future use
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class RuntimeHealth(str, Enum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class CheckStatus(str, Enum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


def _format_datetime(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RuntimeStatus:
    stream_id: str
    status: PublicStreamStatus
    health: RuntimeHealth
    active_variant_count: int
    queue_depth: int
    checks: Mapping[str, CheckStatus]
    started_at: datetime | None = None
    last_poll_at: datetime | None = None
    queue_lag_seconds: float | None = None
    error: str | None = None
    telemetry_available: bool = True
    health_reasons: tuple[str, ...] = ()
    schema_version: str = RUNTIME_STATUS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.active_variant_count < 0:
            raise ValueError("active_variant_count must be >= 0")
        if self.queue_depth < 0:
            raise ValueError("queue_depth must be >= 0")
        if self.queue_lag_seconds is not None and (
            not math.isfinite(self.queue_lag_seconds)
            or self.queue_lag_seconds < 0
        ):
            raise ValueError("queue_lag_seconds must be finite and >= 0")
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stream_id": self.stream_id,
            "status": self.status.value,
            "health": self.health.value,
            "started_at": _format_datetime(self.started_at),
            "last_poll_at": _format_datetime(self.last_poll_at),
            "active_variant_count": self.active_variant_count,
            "queue_depth": self.queue_depth,
            "queue_lag_seconds": self.queue_lag_seconds,
            "error": self.error,
            "telemetry_available": self.telemetry_available,
            "health_reasons": list(self.health_reasons),
            "checks": {
                name: (
                    status.value if isinstance(status, CheckStatus) else str(status)
                )
                for name, status in self.checks.items()
            },
        }

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any

RUNTIME_STATUS_SCHEMA_VERSION = "1.0"
WORKER_ID_REGEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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
    live_edge_lag_seconds: float | None = None
    error: str | None = None
    telemetry_available: bool = True
    health_reasons: tuple[str, ...] = ()
    schema_version: str = RUNTIME_STATUS_SCHEMA_VERSION
    worker_id: str | None = None
    observed_at: datetime | None = None
    admission_mode: str = "coverage"
    startup_segments_outside_scope: int = 0
    dropped_expired_work: int = 0
    dropped_capacity_work: int = 0
    dropped_live_edge_work: int = 0
    coverage_gap_count: int = 0
    coverage_gap_segment_count: int = 0
    dropped_media_segment_count: int = 0
    profile_analysis_total: Mapping[str, int] = field(default_factory=dict)
    active_media_processes: int = 0
    max_media_processes: int = 0

    def __post_init__(self) -> None:
        if self.active_variant_count < 0:
            raise ValueError("active_variant_count must be >= 0")
        if self.queue_depth < 0:
            raise ValueError("queue_depth must be >= 0")
        if self.admission_mode not in {
            "coverage",
            "catch_up",
            "live_edge_protection",
        }:
            raise ValueError("admission_mode is invalid")
        counters = {
            "startup_segments_outside_scope": self.startup_segments_outside_scope,
            "dropped_expired_work": self.dropped_expired_work,
            "dropped_capacity_work": self.dropped_capacity_work,
            "dropped_live_edge_work": self.dropped_live_edge_work,
            "coverage_gap_count": self.coverage_gap_count,
            "coverage_gap_segment_count": self.coverage_gap_segment_count,
            "dropped_media_segment_count": self.dropped_media_segment_count,
            "active_media_processes": self.active_media_processes,
            "max_media_processes": self.max_media_processes,
        }
        if any(value < 0 for value in counters.values()):
            raise ValueError("runtime telemetry counters must be >= 0")
        if self.active_media_processes > self.max_media_processes:
            raise ValueError("active_media_processes cannot exceed maximum")
        if self.queue_lag_seconds is not None and (
            not math.isfinite(self.queue_lag_seconds)
            or self.queue_lag_seconds < 0
        ):
            raise ValueError("queue_lag_seconds must be finite and >= 0")
        if self.live_edge_lag_seconds is not None and (
            not math.isfinite(self.live_edge_lag_seconds)
            or self.live_edge_lag_seconds < 0
        ):
            raise ValueError(
                "live_edge_lag_seconds must be finite and >= 0"
            )
        if self.worker_id is not None:
            if not isinstance(self.worker_id, str) or not WORKER_ID_REGEX.match(self.worker_id):
                raise ValueError(f"Invalid worker_id '{self.worker_id}'")
        if self.observed_at is not None:
            if not isinstance(self.observed_at, datetime):
                raise ValueError("observed_at must be a datetime")
            if self.observed_at.tzinfo is None:
                object.__setattr__(self, "observed_at", self.observed_at.replace(tzinfo=timezone.utc))
            else:
                object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))
        if self.started_at is not None:
            if self.started_at.tzinfo is None:
                object.__setattr__(self, "started_at", self.started_at.replace(tzinfo=timezone.utc))
            else:
                object.__setattr__(self, "started_at", self.started_at.astimezone(timezone.utc))
        if self.last_poll_at is not None:
            if self.last_poll_at.tzinfo is None:
                object.__setattr__(self, "last_poll_at", self.last_poll_at.replace(tzinfo=timezone.utc))
            else:
                object.__setattr__(self, "last_poll_at", self.last_poll_at.astimezone(timezone.utc))
        object.__setattr__(self, "checks", MappingProxyType(dict(self.checks)))
        profile_totals = dict(self.profile_analysis_total)
        if any(
            not isinstance(name, str)
            or not name
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for name, value in profile_totals.items()
        ):
            raise ValueError("profile_analysis_total is invalid")
        object.__setattr__(
            self,
            "profile_analysis_total",
            MappingProxyType(profile_totals),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "stream_id": self.stream_id,
            "status": self.status.value,
            "health": self.health.value,
            "started_at": _format_datetime(self.started_at),
            "last_poll_at": _format_datetime(self.last_poll_at),
            "active_variant_count": self.active_variant_count,
            "queue_depth": self.queue_depth,
            "queue_lag_seconds": self.queue_lag_seconds,
            "live_edge_lag_seconds": self.live_edge_lag_seconds,
            "error": self.error,
            "telemetry_available": self.telemetry_available,
            "health_reasons": list(self.health_reasons),
            "admission_mode": self.admission_mode,
            "startup_segments_outside_scope": (
                self.startup_segments_outside_scope
            ),
            "dropped_expired_work": self.dropped_expired_work,
            "dropped_capacity_work": self.dropped_capacity_work,
            "dropped_live_edge_work": self.dropped_live_edge_work,
            "coverage_gap_count": self.coverage_gap_count,
            "coverage_gap_segment_count": self.coverage_gap_segment_count,
            "dropped_media_segment_count": self.dropped_media_segment_count,
            "profile_analysis_total": dict(self.profile_analysis_total),
            "active_media_processes": self.active_media_processes,
            "max_media_processes": self.max_media_processes,
            "checks": {
                name: (
                    status.value if isinstance(status, CheckStatus) else str(status)
                )
                for name, status in self.checks.items()
            },
        }
        if self.worker_id is not None:
            payload["worker_id"] = self.worker_id
        if self.observed_at is not None:
            payload["observed_at"] = _format_datetime(self.observed_at)
        return payload

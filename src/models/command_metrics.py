from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from models.runtime_status import WORKER_ID_REGEX

COMMAND_METRICS_SCHEMA_VERSION = "1.0"


def _format_time(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CommandMetricsSnapshot:
    worker_id: str
    observed_at: datetime
    # Counters
    command_delivery_received_total: int
    command_applied_total: int
    command_noop_total: int
    command_rejected_total: int
    command_failed_total: int
    command_reclaimed_total: int
    command_duplicate_replay_total: int
    command_dead_letter_total: int
    command_oversized_total: int
    command_stale_total: int
    command_poll_failure_total: int
    command_processing_duration_ms_total: float
    # Gauges & State
    command_pending_count: int
    command_deferred_count: int
    command_processing_duration_ms_max: float
    last_command_processing_duration_ms: float
    last_successful_poll_at: datetime | None = None
    last_command_processed_at: datetime | None = None
    last_error_code: str | None = None
    schema_version: str = COMMAND_METRICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str) or not WORKER_ID_REGEX.match(
            self.worker_id
        ):
            raise ValueError(f"Invalid worker_id '{self.worker_id}'")
        if not isinstance(self.observed_at, datetime):
            raise ValueError("observed_at must be a datetime")

        counters = (
            self.command_delivery_received_total,
            self.command_applied_total,
            self.command_noop_total,
            self.command_rejected_total,
            self.command_failed_total,
            self.command_reclaimed_total,
            self.command_duplicate_replay_total,
            self.command_dead_letter_total,
            self.command_oversized_total,
            self.command_stale_total,
            self.command_poll_failure_total,
            self.command_pending_count,
            self.command_deferred_count,
        )
        if any(not isinstance(value, int) or value < 0 for value in counters):
            raise ValueError("command metric counters and gauges must be non-negative integers")

        durations = (
            self.command_processing_duration_ms_total,
            self.command_processing_duration_ms_max,
            self.last_command_processing_duration_ms,
        )
        if any(
            not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
            for value in durations
        ):
            raise ValueError("command metric durations must be finite and non-negative")

        for name, value in (
            ("last_successful_poll_at", self.last_successful_poll_at),
            ("last_command_processed_at", self.last_command_processed_at),
        ):
            if value is not None and not isinstance(value, datetime):
                raise ValueError(f"{name} must be a datetime or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "worker_id": self.worker_id,
            "observed_at": _format_time(self.observed_at),
            "command_delivery_received_total": self.command_delivery_received_total,
            "command_applied_total": self.command_applied_total,
            "command_noop_total": self.command_noop_total,
            "command_rejected_total": self.command_rejected_total,
            "command_failed_total": self.command_failed_total,
            "command_reclaimed_total": self.command_reclaimed_total,
            "command_duplicate_replay_total": self.command_duplicate_replay_total,
            "command_dead_letter_total": self.command_dead_letter_total,
            "command_oversized_total": self.command_oversized_total,
            "command_stale_total": self.command_stale_total,
            "command_poll_failure_total": self.command_poll_failure_total,
            "command_processing_duration_ms_total": round(self.command_processing_duration_ms_total, 3),
            "command_pending_count": self.command_pending_count,
            "command_deferred_count": self.command_deferred_count,
            "command_processing_duration_ms_max": round(self.command_processing_duration_ms_max, 3),
            "last_command_processing_duration_ms": round(self.last_command_processing_duration_ms, 3),
            "last_successful_poll_at": _format_time(self.last_successful_poll_at),
            "last_command_processed_at": _format_time(self.last_command_processed_at),
            "last_error_code": self.last_error_code,
        }

    def to_redis_hash(self) -> dict[str, str]:
        d = self.to_dict()
        redis_hash: dict[str, str] = {}
        for key, value in d.items():
            if value is None:
                redis_hash[key] = ""
            else:
                redis_hash[key] = str(value)
        return redis_hash

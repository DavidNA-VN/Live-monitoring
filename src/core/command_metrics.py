from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from models.command_metrics import CommandMetricsSnapshot


class CommandMetricsCollector:
    """Thread-safe in-memory metrics collector for monitoring worker command pipeline."""

    def __init__(self) -> None:
        self._lock = Lock()
        # Counters
        self._received_total: int = 0
        self._applied_total: int = 0
        self._noop_total: int = 0
        self._rejected_total: int = 0
        self._failed_total: int = 0
        self._reclaimed_total: int = 0
        self._duplicate_replay_total: int = 0
        self._dead_letter_total: int = 0
        self._oversized_total: int = 0
        self._stale_total: int = 0
        self._poll_failure_total: int = 0
        self._duration_ms_total: float = 0.0
        # Gauges & Timestamps
        self._pending_count: int = 0
        self._deferred_count: int = 0
        self._duration_ms_max: float = 0.0
        self._last_duration_ms: float = 0.0
        self._last_successful_poll_at: datetime | None = None
        self._last_command_processed_at: datetime | None = None
        self._last_error_code: str | None = None

    def record_delivery(self, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            self._received_total += count

    def record_reclaimed(self, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            self._reclaimed_total += count

    def record_result(
        self,
        status: str,
        changed: bool,
        duration_ms: float,
        error_code: str | None = None,
        processed_at: datetime | None = None,
    ) -> None:
        st = str(status).upper()
        if st not in {"APPLIED", "NOOP", "REJECTED", "FAILED"}:
            raise ValueError(f"Unsupported command result status '{status}'")
        with self._lock:
            dur = max(0.0, float(duration_ms))
            self._duration_ms_total += dur
            self._last_duration_ms = dur
            if dur > self._duration_ms_max:
                self._duration_ms_max = dur

            if st == "APPLIED":
                if changed:
                    self._applied_total += 1
                else:
                    self._noop_total += 1
            elif st == "NOOP":
                self._noop_total += 1
            elif st == "REJECTED":
                self._rejected_total += 1
            elif st == "FAILED":
                self._failed_total += 1

            self._last_command_processed_at = processed_at or datetime.now(timezone.utc)
            if error_code:
                self._last_error_code = str(error_code)

    def record_duplicate_replay(self, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            self._duplicate_replay_total += count

    def record_dead_letter(self, error_code: str | None = None, *, oversized: bool = False) -> None:
        with self._lock:
            self._dead_letter_total += 1
            if oversized:
                self._oversized_total += 1
            if error_code:
                self._last_error_code = str(error_code)

    def record_oversized(self) -> None:
        with self._lock:
            self._oversized_total += 1

    def record_stale(self) -> None:
        with self._lock:
            self._stale_total += 1

    def record_poll_success(
        self,
        pending_count: int = 0,
        deferred_count: int = 0,
        at: datetime | None = None,
    ) -> None:
        with self._lock:
            self._pending_count = max(0, int(pending_count))
            self._deferred_count = max(0, int(deferred_count))
            self._last_successful_poll_at = at or datetime.now(timezone.utc)

    def record_poll_failure(self, at: datetime | None = None) -> None:
        with self._lock:
            self._poll_failure_total += 1

    def snapshot(self, worker_id: str, observed_at: datetime | None = None) -> CommandMetricsSnapshot:
        with self._lock:
            return CommandMetricsSnapshot(
                worker_id=worker_id,
                observed_at=observed_at or datetime.now(timezone.utc),
                command_delivery_received_total=self._received_total,
                command_applied_total=self._applied_total,
                command_noop_total=self._noop_total,
                command_rejected_total=self._rejected_total,
                command_failed_total=self._failed_total,
                command_reclaimed_total=self._reclaimed_total,
                command_duplicate_replay_total=self._duplicate_replay_total,
                command_dead_letter_total=self._dead_letter_total,
                command_oversized_total=self._oversized_total,
                command_stale_total=self._stale_total,
                command_poll_failure_total=self._poll_failure_total,
                command_processing_duration_ms_total=self._duration_ms_total,
                command_pending_count=self._pending_count,
                command_deferred_count=self._deferred_count,
                command_processing_duration_ms_max=self._duration_ms_max,
                last_command_processing_duration_ms=self._last_duration_ms,
                last_successful_poll_at=self._last_successful_poll_at,
                last_command_processed_at=self._last_command_processed_at,
                last_error_code=self._last_error_code,
            )

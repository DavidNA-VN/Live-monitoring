from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
import logging
from threading import Event
from typing import Callable

import redis

from app.redis_runtime_status_projector import RuntimeStatusProjectionError
from core.runtime_status_projector import RuntimeStatusProjector
from core.runtime_status_reader import RuntimeStatusReader
from core.stream_supervisor import StreamSupervisor
from models.runtime_status import WORKER_ID_REGEX

logger = logging.getLogger(__name__)


class RuntimeStatusProjectionService:
    """Periodically reads in-memory supervisor statuses and projects them to the public read plane."""

    def __init__(
        self,
        *,
        worker_id: str,
        supervisor: StreamSupervisor,
        reader: RuntimeStatusReader,
        projector: RuntimeStatusProjector,
        projection_interval: float = 2.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not WORKER_ID_REGEX.match(worker_id):
            raise ValueError(f"Invalid worker_id '{worker_id}'")
        if projection_interval <= 0:
            raise ValueError("projection_interval must be > 0")

        self.worker_id = worker_id
        self.supervisor = supervisor
        self.reader = reader
        self.projector = projector
        self.projection_interval = projection_interval
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._known_stream_ids: set[str] = set()
        self._wake_event: Event = Event()

    def _now(self) -> datetime:
        dt = self._clock()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def wake(self) -> None:
        """Signal the background projection loop to wake up immediately."""
        self._wake_event.set()

    def project_once(self) -> None:
        """Run a single projection cycle for all currently active and recently removed streams."""
        now = self._now()
        current_stream_ids = set(self.supervisor.snapshots().keys())

        # Project all active stream statuses
        for stream_id in current_stream_ids:
            try:
                raw_status = self.reader.get(stream_id)
                if raw_status is None:
                    continue
                projected = dataclasses.replace(
                    raw_status,
                    worker_id=self.worker_id,
                    observed_at=now,
                )
                self.projector.project(projected)
            except (redis.RedisError, RuntimeStatusProjectionError) as exc:
                logger.warning(
                    "Projection failed for stream '%s': %s",
                    stream_id,
                    exc,
                )

        # Detect streams stopped/removed since last cycle
        removed_stream_ids = self._known_stream_ids - current_stream_ids
        failed_removals: set[str] = set()
        for rem_id in removed_stream_ids:
            try:
                self.projector.remove(
                    stream_id=rem_id,
                    worker_id=self.worker_id,
                    observed_at=now,
                )
            except (redis.RedisError, RuntimeStatusProjectionError) as exc:
                failed_removals.add(rem_id)
                logger.warning(
                    "Remove projection failed for stream '%s': %s",
                    rem_id,
                    exc,
                )

        self._known_stream_ids = current_stream_ids | failed_removals

    def run(self, stop_event: Event) -> None:
        """Main service loop running until stop_event is set."""
        while not stop_event.is_set():
            try:
                self.project_once()
            except Exception:
                logger.exception("Unexpected exception in projection cycle")

            self._wake_event.wait(timeout=self.projection_interval)
            self._wake_event.clear()

        # Capture the latest observed lifecycle state without treating worker
        # shutdown as stream removal. Supervisor slots are still present here.
        try:
            self.project_once()
        except Exception:
            logger.exception("Unexpected exception in final projection cycle")

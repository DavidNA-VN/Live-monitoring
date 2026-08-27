from __future__ import annotations

from datetime import datetime, timezone
import logging
from threading import Event
from typing import Callable

from core.media_process_budget import ObservableProcessGate
from core.stream_supervisor import StreamSupervisor
from core.worker_heartbeat import (
    WorkerHeartbeatPublisher,
    WorkerHeartbeatPublishError,
)
from models.runtime_status import WORKER_ID_REGEX
from models.worker_heartbeat import WorkerHeartbeat, WorkerState

logger = logging.getLogger(__name__)


class WorkerHeartbeatService:
    """Manages worker lifecycle states, capacity snapshots, and periodic heartbeat publishing."""

    def __init__(
        self,
        *,
        worker_id: str,
        supervisor: StreamSupervisor,
        media_gate: ObservableProcessGate,
        publisher: WorkerHeartbeatPublisher,
        version: str = "dev",
        command_consumer_ready_provider: Callable[[], bool] | None = None,
        command_consumer_required: bool = True,
        heartbeat_interval: float = 5.0,
        heartbeat_ttl: int = 15,
        discovery_window: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or not WORKER_ID_REGEX.match(worker_id):
            raise ValueError(f"Invalid worker_id '{worker_id}'")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be > 0")
        if heartbeat_ttl <= heartbeat_interval:
            raise ValueError("heartbeat_ttl must be > heartbeat_interval")
        if discovery_window < heartbeat_ttl:
            raise ValueError("discovery_window must be >= heartbeat_ttl")

        self.worker_id = worker_id
        self.version = version
        self.supervisor = supervisor
        self.media_gate = media_gate
        self.publisher = publisher
        self.command_consumer_ready_provider = command_consumer_ready_provider
        self.command_consumer_required = command_consumer_required
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_ttl = heartbeat_ttl
        self.discovery_window = discovery_window
        self._clock = clock or (lambda: datetime.now(timezone.utc))

        self.state: WorkerState = WorkerState.STARTING
        self._has_published_ready = False
        self._stopping_event = Event()
        self.started_at: datetime = self._now()

    def _now(self) -> datetime:
        dt = self._clock()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _evaluate_state(self) -> tuple[WorkerState, bool]:
        consumer_ready = (
            self.command_consumer_ready_provider()
            if self.command_consumer_ready_provider is not None
            else False
        )
        if self._stopping_event.is_set():
            return WorkerState.STOPPING, consumer_ready
        if self.command_consumer_required:
            if consumer_ready:
                return WorkerState.READY, consumer_ready
            if self._has_published_ready:
                return WorkerState.DEGRADED, consumer_ready
            return WorkerState.STARTING, consumer_ready
        return WorkerState.READY, consumer_ready

    def request_stopping(self) -> None:
        """Latch STOPPING so no later periodic heartbeat can return to READY."""
        self._stopping_event.set()

    def build_heartbeat(self, state: WorkerState | None = None) -> WorkerHeartbeat:
        if state is not None:
            target_state = state
            consumer_ready = (
                self.command_consumer_ready_provider()
                if self.command_consumer_ready_provider is not None
                else False
            )
        else:
            target_state, consumer_ready = self._evaluate_state()

        now = self._now()
        active_stream_count = self.supervisor.stream_count()
        media_snapshot = self.media_gate.snapshot()

        return WorkerHeartbeat(
            worker_id=self.worker_id,
            state=target_state,
            started_at=self.started_at,
            last_seen_at=now,
            command_consumer_ready=consumer_ready,
            active_stream_count=active_stream_count,
            max_streams=self.supervisor.max_streams,
            active_media_processes=media_snapshot.active,
            max_media_processes=media_snapshot.maximum,
            version=self.version,
        )

    def publish_heartbeat(
        self,
        state: WorkerState | None = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        heartbeat = self.build_heartbeat(state=state)
        try:
            self.publisher.publish(
                heartbeat,
                ttl_seconds=(
                    self.heartbeat_ttl
                    if ttl_seconds is None
                    else ttl_seconds
                ),
                discovery_window_seconds=self.discovery_window,
            )
        except WorkerHeartbeatPublishError as exc:
            logger.warning(
                "Heartbeat publishing failed for worker '%s': %s",
                self.worker_id,
                exc,
            )
            return False

        self.state = heartbeat.state
        if heartbeat.state == WorkerState.READY:
            self._has_published_ready = True
        return True

    def run(self, stop_event: Event) -> None:
        """Periodic heartbeat loop until stop_event is signaled."""
        initial_state = (
            WorkerState.STOPPING
            if self._stopping_event.is_set()
            else WorkerState.STARTING
        )
        self.publish_heartbeat(state=initial_state)

        while not stop_event.is_set():
            stop_event.wait(timeout=self.heartbeat_interval)
            if not stop_event.is_set():
                self.publish_heartbeat()

        # Graceful shutdown state
        self.request_stopping()
        self.publish_heartbeat(
            state=WorkerState.STOPPING,
            ttl_seconds=min(self.heartbeat_ttl, 15),
        )

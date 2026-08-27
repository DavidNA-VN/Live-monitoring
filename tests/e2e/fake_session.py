from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any

from core.stream_session import (
    StreamSession,
    StreamSessionSnapshot,
    StreamSessionStatus,
)
from core.stream_supervisor import StreamSessionFactory
from models.stream_config import StreamConfig


class FakeStreamSession:
    """Thread-safe fake session that models lifecycle state without spawning FFmpeg or media threads."""

    def __init__(self, config: StreamConfig, factory: FakeSessionFactory) -> None:
        self.config = config
        self.stream_id = config.identity.external_stream_id
        self.factory = factory
        self._status = StreamSessionStatus.CREATED
        self._started_at: datetime | None = None
        self._error: str | None = None
        self._lock = Lock()

    def start(self) -> None:
        with self._lock:
            if self._status != StreamSessionStatus.CREATED:
                raise RuntimeError(f"Cannot start session in status {self._status}")
            self._status = StreamSessionStatus.RUNNING
            self._started_at = datetime.now(timezone.utc)
            with self.factory.lock:
                self.factory.started_count += 1
                self.factory.active_count += 1

    def stop(self, *, timeout: float | None = None, paused: bool = False) -> bool:
        target = StreamSessionStatus.PAUSED if paused else StreamSessionStatus.STOPPED
        with self._lock:
            prev = self._status
            self._status = target
            if prev == StreamSessionStatus.RUNNING:
                with self.factory.lock:
                    self.factory.stopped_count += 1
                    self.factory.active_count -= 1
        return True

    def snapshot(self) -> StreamSessionSnapshot:
        with self._lock:
            return StreamSessionSnapshot(
                stream_id=self.stream_id,
                status=self._status,
                error=self._error,
                started_at=self._started_at,
            )


class FakeSessionFactory(StreamSessionFactory):
    """Tracks session creation and lifecycle counters to verify zero duplicate sessions."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.created_count = 0
        self.started_count = 0
        self.stopped_count = 0
        self.active_count = 0
        self.sessions: dict[str, FakeStreamSession] = {}

    def create(self, config: StreamConfig) -> StreamSession:
        with self.lock:
            self.created_count += 1
            session = FakeStreamSession(config, self)
            self.sessions[config.identity.external_stream_id] = session
            return session  # type: ignore[return-value]

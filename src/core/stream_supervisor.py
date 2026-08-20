from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from core.stream_session import (
    StreamSession,
    StreamSessionSnapshot,
    StreamSessionStatus,
)
from models.stream_config import StreamConfig


class StreamSessionFactory(Protocol):
    def create(self, config: StreamConfig) -> StreamSession:
        ...


@dataclass
class _StreamSlot:
    config: StreamConfig
    session: StreamSession | None = None
    idle_status: StreamSessionStatus = StreamSessionStatus.CREATED
    desired_running: bool = False
    error: str | None = None


class StreamSupervisor:
    def __init__(
        self,
        *,
        session_factory: StreamSessionFactory,
        max_streams: int = 16,
        shutdown_timeout: float = 30.0,
    ) -> None:
        if max_streams <= 0:
            raise ValueError("max_streams must be > 0")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be > 0")
        self.session_factory = session_factory
        self.max_streams = max_streams
        self.shutdown_timeout = shutdown_timeout
        self._slots: dict[str, _StreamSlot] = {}
        self._lock = Lock()

    def add(self, config: StreamConfig, *, start: bool = True) -> str:
        stream_id = config.identity.stream_id
        with self._lock:
            if stream_id in self._slots:
                raise ValueError(f"Stream already exists: {stream_id}")
            if len(self._slots) >= self.max_streams:
                raise RuntimeError("Stream supervisor capacity reached")
            slot = _StreamSlot(
                config=config,
                idle_status=(
                    StreamSessionStatus.CREATED
                    if config.enabled
                    else StreamSessionStatus.PAUSED
                ),
                desired_running=start and config.enabled,
            )
            self._slots[stream_id] = slot
        if start and config.enabled:
            self._start_slot(stream_id)
        return stream_id

    def remove(self, stream_id: str) -> bool:
        with self._lock:
            slot = self._slots.get(stream_id)
        if slot is None:
            return False
        if slot.session is not None and not slot.session.stop(
            timeout=self.shutdown_timeout
        ):
            return False
        with self._lock:
            self._slots.pop(stream_id, None)
        return True

    def pause(self, stream_id: str) -> bool:
        with self._lock:
            slot = self._require_slot(stream_id)
            session = slot.session
            slot.desired_running = False
        if session is None:
            with self._lock:
                slot.idle_status = StreamSessionStatus.PAUSED
            return True
        stopped = session.stop(
            timeout=self.shutdown_timeout, paused=True
        )
        if stopped:
            with self._lock:
                slot.session = None
                slot.idle_status = StreamSessionStatus.PAUSED
        return stopped

    def resume(self, stream_id: str) -> None:
        with self._lock:
            slot = self._require_slot(stream_id)
            if slot.session is not None:
                return
            if not slot.config.enabled:
                raise RuntimeError("Disabled stream must be updated before resume")
            slot.desired_running = True
        self._start_slot(stream_id)

    def update(self, stream_id: str, config: StreamConfig) -> None:
        if config.identity.stream_id != stream_id:
            raise ValueError("Updated config must keep the same stream identity")
        with self._lock:
            slot = self._require_slot(stream_id)
            previous = slot.session
            restart = slot.desired_running and config.enabled
        if previous is not None:
            if not previous.stop(timeout=self.shutdown_timeout):
                raise TimeoutError(f"Stream did not drain: {stream_id}")
        with self._lock:
            slot.session = None
            slot.config = config
            slot.error = None
            slot.idle_status = (
                StreamSessionStatus.CREATED
                if restart
                else StreamSessionStatus.PAUSED
            )
            slot.desired_running = restart
        if restart:
            self._start_slot(stream_id)

    def stop_all(self) -> bool:
        with self._lock:
            for slot in self._slots.values():
                slot.desired_running = False
            sessions = [
                (stream_id, slot.session)
                for stream_id, slot in self._slots.items()
                if slot.session is not None
            ]
        stopped = True
        for stream_id, session in sessions:
            session_stopped = session.stop(timeout=self.shutdown_timeout)
            stopped = session_stopped and stopped
            if session_stopped:
                with self._lock:
                    slot = self._slots.get(stream_id)
                    if slot is not None and slot.session is session:
                        slot.session = None
                        slot.idle_status = StreamSessionStatus.STOPPED
        return stopped

    def start_all(self) -> dict[str, str]:
        with self._lock:
            stream_ids = [
                stream_id
                for stream_id, slot in self._slots.items()
                if slot.config.enabled and slot.session is None
            ]
            for stream_id in stream_ids:
                self._slots[stream_id].desired_running = True
        errors = {}
        for stream_id in stream_ids:
            try:
                self._start_slot(stream_id)
            except Exception as exc:
                errors[stream_id] = str(exc)
        return errors

    def snapshots(self) -> dict[str, StreamSessionSnapshot]:
        with self._lock:
            return {
                stream_id: (
                    slot.session.snapshot()
                    if slot.session is not None
                    else StreamSessionSnapshot(
                        stream_id=stream_id,
                        status=slot.idle_status,
                        error=slot.error,
                    )
                )
                for stream_id, slot in self._slots.items()
            }

    def is_healthy(self) -> bool:
        with self._lock:
            slots = tuple(self._slots.values())
        for slot in slots:
            if not slot.desired_running:
                continue
            if slot.session is None:
                return False
            if slot.session.snapshot().status != StreamSessionStatus.RUNNING:
                return False
        return True

    def _start_slot(self, stream_id: str) -> None:
        with self._lock:
            slot = self._require_slot(stream_id)
            if slot.session is not None:
                return
            if not slot.desired_running:
                return
            config = slot.config
        session = None
        try:
            session = self.session_factory.create(config)
            session.start()
        except Exception as exc:
            if session is not None:
                session.stop(timeout=self.shutdown_timeout)
            with self._lock:
                current = self._slots.get(stream_id)
                if current is slot and current.desired_running:
                    current.idle_status = StreamSessionStatus.FAILED
                    current.error = str(exc)
            raise

        with self._lock:
            current = self._slots.get(stream_id)
            accepted = (
                current is slot
                and current.session is None
                and current.desired_running
                and current.config == config
            )
            concurrent_start = (
                current is slot and current.session is not None
            )
            if accepted:
                current.session = session
                current.error = None
                return

        # A concurrent pause/remove/update invalidated this startup attempt.
        session.stop(timeout=self.shutdown_timeout)
        if concurrent_start:
            raise RuntimeError(f"Concurrent stream start: {stream_id}")

    def _require_slot(self, stream_id: str) -> _StreamSlot:
        try:
            return self._slots[stream_id]
        except KeyError as exc:
            raise KeyError(f"Unknown stream: {stream_id}") from exc

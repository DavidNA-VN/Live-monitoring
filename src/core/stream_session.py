from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import logging
from threading import Lock, Thread
from typing import Protocol

from models.stream_config import StreamConfig


logger = logging.getLogger(__name__)


class RuntimeLifecycle(Protocol):
    def run_forever(self) -> None:
        ...

    def stop(self) -> None:
        ...


class StreamSessionStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class StreamSessionSnapshot:
    stream_id: str
    status: StreamSessionStatus
    error: str | None = None


class StreamSession:
    """Owns one stream runtime thread and its closeable resources."""

    def __init__(
        self,
        *,
        config: StreamConfig,
        runtime: RuntimeLifecycle,
        close_callbacks: tuple[Callable[[], None], ...] = (),
    ) -> None:
        self.config = config
        self.stream_id = config.identity.stream_id
        self.runtime = runtime
        self.close_callbacks = close_callbacks
        self._status = StreamSessionStatus.CREATED
        self._error: str | None = None
        self._target_status = StreamSessionStatus.STOPPED
        self._thread: Thread | None = None
        self._lock = Lock()
        self._closed = False

    def start(self) -> None:
        with self._lock:
            if self._status != StreamSessionStatus.CREATED:
                raise RuntimeError(
                    f"Cannot start session in state {self._status.value}"
                )
            self._status = StreamSessionStatus.RUNNING
            self._thread = Thread(
                target=self._run,
                name=f"stream-session-{self.stream_id[:12]}",
                daemon=False,
            )
            self._thread.start()

    def stop(
        self,
        *,
        timeout: float | None = None,
        paused: bool = False,
    ) -> bool:
        target = (
            StreamSessionStatus.PAUSED
            if paused
            else StreamSessionStatus.STOPPED
        )
        with self._lock:
            self._target_status = target
            if self._status == StreamSessionStatus.CREATED:
                self._status = target
                thread = None
            elif self._status in (
                StreamSessionStatus.STOPPED,
                StreamSessionStatus.PAUSED,
                StreamSessionStatus.FAILED,
            ):
                thread = self._thread
            else:
                self._status = StreamSessionStatus.STOPPING
                thread = self._thread
        self.runtime.stop()
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                return False
        else:
            self._close_resources()
        return True

    def snapshot(self) -> StreamSessionSnapshot:
        with self._lock:
            return StreamSessionSnapshot(
                stream_id=self.stream_id,
                status=self._status,
                error=self._error,
            )

    def _run(self) -> None:
        try:
            self.runtime.run_forever()
        except Exception as exc:
            logger.exception(
                "Stream session failed stream_id=%s",
                self.stream_id,
                extra={
                    "event_name": "stream_session_failed",
                    "stream_id": self.stream_id,
                },
            )
            with self._lock:
                self._status = StreamSessionStatus.FAILED
                self._error = str(exc)
        else:
            with self._lock:
                self._status = self._target_status
        finally:
            self._close_resources()

    def _close_resources(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for close in self.close_callbacks:
            try:
                close()
            except Exception:
                logger.exception(
                    "Unable to close stream resource stream_id=%s",
                    self.stream_id,
                )

from __future__ import annotations

from threading import Lock

from core.monitoring_control import (
    MonitoringAction,
    MonitoringControl,
    MonitoringControlResult,
    StreamAlreadyExistsError,
    StreamCapacityError,
    StreamIdentityMismatchError,
    StreamNotFoundError,
    StreamOperationError,
)
from core.stream_session import StreamSessionStatus
from core.stream_supervisor import (
    StreamSupervisor,
    StreamSupervisorAlreadyRegisteredError,
    StreamSupervisorCapacityError,
)
from models.stream_config import StreamConfig


class SupervisorMonitoringControl:
    """Adapter exposing the stable MonitoringControl port around StreamSupervisor."""

    def __init__(
        self,
        supervisor: StreamSupervisor,
        *,
        lock_stripes: int = 64,
    ) -> None:
        if lock_stripes <= 0:
            raise ValueError("lock_stripes must be > 0")
        self.supervisor = supervisor
        self._operation_locks = tuple(Lock() for _ in range(lock_stripes))

    def start(self, config: StreamConfig) -> MonitoringControlResult:
        stream_id = config.identity.external_stream_id
        with self._operation_lock(stream_id):
            return self._start_locked(stream_id, config)

    def _start_locked(
        self,
        stream_id: str,
        config: StreamConfig,
    ) -> MonitoringControlResult:
        if not config.enabled:
            raise StreamOperationError(
                f"Cannot start disabled stream: {stream_id}"
            )
        existing_config = self.supervisor.configuration(stream_id)
        if existing_config is not None:
            if existing_config != config:
                raise StreamAlreadyExistsError(
                    f"Stream already exists with different configuration: {stream_id}"
                )
            snap = self.supervisor.snapshot(stream_id)
            if snap is None:
                pass
            elif snap.status == StreamSessionStatus.RUNNING:
                return MonitoringControlResult(
                    stream_id=stream_id,
                    action=MonitoringAction.START,
                    changed=False,
                )
            elif snap.status in (
                StreamSessionStatus.PAUSED,
                StreamSessionStatus.CREATED,
                StreamSessionStatus.STOPPED,
            ):
                try:
                    self.supervisor.resume(stream_id)
                    return MonitoringControlResult(
                        stream_id=stream_id,
                        action=MonitoringAction.START,
                        changed=True,
                    )
                except KeyError as exc:
                    raise StreamNotFoundError(stream_id) from exc
                except Exception as exc:
                    raise StreamOperationError(stream_id) from exc
            elif snap.status == StreamSessionStatus.FAILED:
                removed = self.supervisor.remove(stream_id)
                if not removed:
                    raise StreamOperationError(
                        f"Failed stream could not be cleaned up for restart: {stream_id}"
                    )
            elif snap.status == StreamSessionStatus.STOPPING:
                raise StreamOperationError(
                    f"Stream is currently stopping: {stream_id}"
                )

        try:
            self.supervisor.add(config, start=True)
            return MonitoringControlResult(
                stream_id=stream_id,
                action=MonitoringAction.START,
                changed=True,
            )
        except StreamSupervisorAlreadyRegisteredError as exc:
            current_config = self.supervisor.configuration(stream_id)
            if current_config is not None and current_config == config:
                snap = self.supervisor.snapshot(stream_id)
                if snap is not None and snap.status == StreamSessionStatus.RUNNING:
                    return MonitoringControlResult(
                        stream_id=stream_id,
                        action=MonitoringAction.START,
                        changed=False,
                    )
            raise StreamAlreadyExistsError(stream_id) from exc
        except StreamSupervisorCapacityError as exc:
            raise StreamCapacityError(stream_id) from exc
        except Exception as exc:
            if isinstance(
                exc,
                (
                    StreamAlreadyExistsError,
                    StreamCapacityError,
                    StreamOperationError,
                ),
            ):
                raise
            raise StreamOperationError(stream_id) from exc

    def pause(self, stream_id: str) -> MonitoringControlResult:
        with self._operation_lock(stream_id):
            return self._pause_locked(stream_id)

    def _pause_locked(self, stream_id: str) -> MonitoringControlResult:
        snap = self.supervisor.snapshot(stream_id)
        if snap is None:
            raise StreamNotFoundError(f"Stream not found: {stream_id}")
        if snap.status in (
            StreamSessionStatus.PAUSED,
            StreamSessionStatus.FAILED,
            StreamSessionStatus.STOPPED,
        ):
            return MonitoringControlResult(
                stream_id=stream_id,
                action=MonitoringAction.PAUSE,
                changed=False,
            )
        if snap.status == StreamSessionStatus.STOPPING:
            raise StreamOperationError(
                f"Stream is currently stopping: {stream_id}"
            )
        try:
            paused = self.supervisor.pause(stream_id)
            if not paused:
                raise StreamOperationError(
                    f"Failed to pause stream: {stream_id}"
                )
            return MonitoringControlResult(
                stream_id=stream_id,
                action=MonitoringAction.PAUSE,
                changed=True,
            )
        except KeyError as exc:
            raise StreamNotFoundError(stream_id) from exc
        except Exception as exc:
            if isinstance(exc, (StreamNotFoundError, StreamOperationError)):
                raise
            raise StreamOperationError(stream_id) from exc

    def resume(self, stream_id: str) -> MonitoringControlResult:
        with self._operation_lock(stream_id):
            return self._resume_locked(stream_id)

    def _resume_locked(self, stream_id: str) -> MonitoringControlResult:
        snap = self.supervisor.snapshot(stream_id)
        if snap is None:
            raise StreamNotFoundError(f"Stream not found: {stream_id}")
        if snap.status == StreamSessionStatus.RUNNING:
            return MonitoringControlResult(
                stream_id=stream_id,
                action=MonitoringAction.RESUME,
                changed=False,
            )
        if snap.status == StreamSessionStatus.STOPPING:
            raise StreamOperationError(
                f"Stream is currently stopping: {stream_id}"
            )
        if snap.status == StreamSessionStatus.FAILED:
            raise StreamOperationError(
                f"Failed stream must be started again: {stream_id}"
            )
        try:
            self.supervisor.resume(stream_id)
            return MonitoringControlResult(
                stream_id=stream_id,
                action=MonitoringAction.RESUME,
                changed=True,
            )
        except KeyError as exc:
            raise StreamNotFoundError(stream_id) from exc
        except Exception as exc:
            if isinstance(exc, (StreamNotFoundError, StreamOperationError)):
                raise
            raise StreamOperationError(stream_id) from exc

    def stop(self, stream_id: str) -> MonitoringControlResult:
        with self._operation_lock(stream_id):
            return self._stop_locked(stream_id)

    def _stop_locked(self, stream_id: str) -> MonitoringControlResult:
        snap = self.supervisor.snapshot(stream_id)
        if snap is None:
            return MonitoringControlResult(
                stream_id=stream_id,
                action=MonitoringAction.STOP,
                changed=False,
            )
        try:
            removed = self.supervisor.remove(stream_id)
            if not removed:
                raise StreamOperationError(
                    f"Stream did not drain: {stream_id}"
                )
            return MonitoringControlResult(
                stream_id=stream_id,
                action=MonitoringAction.STOP,
                changed=True,
            )
        except Exception as exc:
            if isinstance(exc, StreamOperationError):
                raise
            raise StreamOperationError(stream_id) from exc

    def update_config(
        self,
        stream_id: str,
        config: StreamConfig,
    ) -> MonitoringControlResult:
        if config.identity.external_stream_id != stream_id:
            raise StreamIdentityMismatchError(
                f"Config stream_id '{config.identity.external_stream_id}' does not match route '{stream_id}'"
            )
        with self._operation_lock(stream_id):
            return self._update_config_locked(stream_id, config)

    def _update_config_locked(
        self,
        stream_id: str,
        config: StreamConfig,
    ) -> MonitoringControlResult:
        current_config = self.supervisor.configuration(stream_id)
        if current_config is None:
            raise StreamNotFoundError(f"Stream not found: {stream_id}")
        if current_config == config:
            return MonitoringControlResult(
                stream_id=stream_id,
                action=MonitoringAction.UPDATE_CONFIG,
                changed=False,
            )
        try:
            self.supervisor.update(stream_id, config)
            return MonitoringControlResult(
                stream_id=stream_id,
                action=MonitoringAction.UPDATE_CONFIG,
                changed=True,
            )
        except KeyError as exc:
            raise StreamNotFoundError(stream_id) from exc
        except ValueError as exc:
            raise StreamIdentityMismatchError(stream_id) from exc
        except TimeoutError as exc:
            raise StreamOperationError(
                f"Stream did not drain: {stream_id}"
            ) from exc
        except Exception as exc:
            if isinstance(
                exc,
                (
                    StreamNotFoundError,
                    StreamIdentityMismatchError,
                    StreamOperationError,
                ),
            ):
                raise
            raise StreamOperationError(stream_id) from exc

    def _operation_lock(self, stream_id: str) -> Lock:
        return self._operation_locks[hash(stream_id) % len(self._operation_locks)]

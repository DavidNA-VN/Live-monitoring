from __future__ import annotations

from threading import Event
from uuid import uuid4

from app.monitoring_command_handler import MonitoringCommandHandler
from app.monitoring_session_factory import MonitoringSessionFactory
from app.redis_monitoring_command_consumer import RedisMonitoringCommandConsumer
from app.redis_runtime_status_projector import RedisRuntimeStatusProjector
from app.runtime_status_projection_service import RuntimeStatusProjectionService
from app.supervisor_monitoring_control import SupervisorMonitoringControl
from app.supervisor_runtime_status import SupervisorRuntimeStatusReader
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import (
    ControlRedisKeys,
    PublicRuntimeRedisKeys,
    RedisNamespace,
    RuntimeRedisKeys,
)
from core.stream_supervisor import StreamSupervisor
from models.runtime_status import WORKER_ID_REGEX


class MonitoringWorkerApplication:
    """Composition root for lifecycle, status, projection, and Redis command delivery."""

    def __init__(
        self,
        *,
        redis_settings: RedisSettings | None = None,
        namespace: RedisNamespace | None = None,
        max_streams: int = 16,
        max_concurrent_media_processes: int = 8,
        consumer_name: str | None = None,
        worker_id: str | None = None,
        projection_interval: float = 2.0,
        status_stream_max_length: int = 10_000,
    ) -> None:
        self.namespace = namespace or RedisNamespace()
        self.worker_id = worker_id or f"worker-{uuid4().hex[:12]}"
        if not WORKER_ID_REGEX.match(self.worker_id):
            raise ValueError(f"Invalid worker_id '{self.worker_id}'")

        self.redis_client = RedisClient(redis_settings)
        self.supervisor = StreamSupervisor(
            session_factory=MonitoringSessionFactory(
                redis_settings=redis_settings,
                namespace=self.namespace,
                max_concurrent_media_processes=max_concurrent_media_processes,
            ),
            max_streams=max_streams,
        )
        self.control = SupervisorMonitoringControl(self.supervisor)
        self.runtime_status = SupervisorRuntimeStatusReader(
            supervisor=self.supervisor,
            redis_client=self.redis_client,
            runtime_keys=RuntimeRedisKeys(self.namespace),
        )
        self.public_runtime_keys = PublicRuntimeRedisKeys(self.namespace)
        self.projector = RedisRuntimeStatusProjector(
            redis_client=self.redis_client,
            keys=self.public_runtime_keys,
            status_stream_max_length=status_stream_max_length,
        )
        self.projection_service = RuntimeStatusProjectionService(
            worker_id=self.worker_id,
            supervisor=self.supervisor,
            reader=self.runtime_status,
            projector=self.projector,
            projection_interval=projection_interval,
        )
        self.command_handler = MonitoringCommandHandler(self.control)
        self.command_consumer = RedisMonitoringCommandConsumer(
            redis_client=self.redis_client,
            handler=self.command_handler,
            keys=ControlRedisKeys(self.namespace),
            consumer_name=consumer_name or f"{self.worker_id}-commands",
            on_command_finalized=self.projection_service.wake,
        )
        self._closed = False

    def ping(self) -> None:
        self.redis_client.ping()

    def run_commands(self, stop_event: Event) -> None:
        self.command_consumer.run(stop_event)

    def run_projection(self, stop_event: Event) -> None:
        self.projection_service.run(stop_event)

    def close(self) -> bool:
        if self._closed:
            return True
        stopped = self.supervisor.stop_all()
        if not stopped:
            return False
        self.redis_client.close()
        self._closed = True
        return True

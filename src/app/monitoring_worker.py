from __future__ import annotations

from threading import Event
from uuid import uuid4

from app.monitoring_command_handler import MonitoringCommandHandler
from app.monitoring_session_factory import MonitoringSessionFactory
from app.redis_monitoring_command_consumer import RedisMonitoringCommandConsumer
from app.supervisor_monitoring_control import SupervisorMonitoringControl
from app.supervisor_runtime_status import SupervisorRuntimeStatusReader
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import ControlRedisKeys, RedisNamespace, RuntimeRedisKeys
from core.stream_supervisor import StreamSupervisor


class MonitoringWorkerApplication:
    """Composition root for lifecycle, status and Redis command delivery."""

    def __init__(
        self,
        *,
        redis_settings: RedisSettings | None = None,
        namespace: RedisNamespace | None = None,
        max_streams: int = 16,
        max_concurrent_media_processes: int = 8,
        consumer_name: str | None = None,
    ) -> None:
        self.namespace = namespace or RedisNamespace()
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
        self.command_handler = MonitoringCommandHandler(self.control)
        self.command_consumer = RedisMonitoringCommandConsumer(
            redis_client=self.redis_client,
            handler=self.command_handler,
            keys=ControlRedisKeys(self.namespace),
            consumer_name=consumer_name or f"worker-{uuid4().hex[:12]}",
        )
        self._closed = False

    def ping(self) -> None:
        self.redis_client.ping()

    def run_commands(self, stop_event: Event) -> None:
        self.command_consumer.run(stop_event)

    def close(self) -> bool:
        if self._closed:
            return True
        stopped = self.supervisor.stop_all()
        if not stopped:
            return False
        self.redis_client.close()
        self._closed = True
        return True

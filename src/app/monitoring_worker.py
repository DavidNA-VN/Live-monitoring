from __future__ import annotations

from threading import Event
import logging
from uuid import uuid4

from app.command_guardrails import CommandGuardrails
from app.monitoring_command_handler import MonitoringCommandHandler
from app.monitoring_session_factory import MonitoringSessionFactory
from app.persistent_monitoring_command_handler import (
    PersistentMonitoringCommandHandler,
)
from app.redis_desired_state_repository import RedisDesiredStateRepository
from app.redis_monitoring_command_consumer import RedisMonitoringCommandConsumer
from app.redis_runtime_status_projector import RedisRuntimeStatusProjector
from app.redis_worker_command_metrics_publisher import (
    RedisWorkerCommandMetricsPublisher,
)
from app.redis_worker_heartbeat_publisher import RedisWorkerHeartbeatPublisher
from app.runtime_status_projection_service import RuntimeStatusProjectionService
from app.supervisor_desired_state_reconciler import (
    SupervisorDesiredStateReconciler,
)
from app.supervisor_monitoring_control import SupervisorMonitoringControl
from app.supervisor_runtime_status import SupervisorRuntimeStatusReader
from app.worker_heartbeat_service import WorkerHeartbeatService
from core.command_metrics import CommandMetricsCollector
from core.desired_state_reconciler import RecoveryReport
from core.desired_state_repository import DesiredStateUnavailableError
from core.media_process_budget import ObservableProcessGate
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import (
    ControlRedisKeys,
    DesiredStateRedisKeys,
    PublicRuntimeRedisKeys,
    RedisNamespace,
    RuntimeRedisKeys,
    WorkerRedisKeys,
)
from core.stream_supervisor import StreamSessionFactory, StreamSupervisor
from models.runtime_status import WORKER_ID_REGEX
from models.analysis import (
    AnalysisResourceClass,
    ResourcePoolLimit,
    default_resource_limits,
)


logger = logging.getLogger(__name__)


class MonitoringWorkerApplication:
    """Composition root for lifecycle, status, projection, heartbeat, desired recovery, and Redis command delivery."""

    def __init__(
        self,
        *,
        redis_settings: RedisSettings | None = None,
        namespace: RedisNamespace | None = None,
        session_factory: StreamSessionFactory | None = None,
        max_streams: int = 16,
        max_concurrent_media_processes: int = 8,
        per_stream_media_processes: int = 4,
        video_decode_workers: int = 4,
        audio_decode_workers: int = 1,
        consumer_name: str | None = None,
        worker_id: str | None = None,
        worker_version: str = "dev",
        command_consumer_required: bool = True,
        projection_interval: float = 2.0,
        status_stream_max_length: int = 10_000,
        heartbeat_interval: float = 5.0,
        heartbeat_ttl: int = 15,
        worker_discovery_window: int = 30,
        max_command_payload_bytes: int = 65_536,
        max_command_age_seconds: float | None = None,
        command_metrics_ttl: int = 120,
    ) -> None:
        self.namespace = namespace or RedisNamespace()
        self.worker_id = worker_id or f"worker-{uuid4().hex[:12]}"
        if not WORKER_ID_REGEX.match(self.worker_id):
            raise ValueError(f"Invalid worker_id '{self.worker_id}'")
        self.worker_version = worker_version

        self.media_gate = ObservableProcessGate(max_concurrent_media_processes)
        self.redis_client = RedisClient(redis_settings)
        resource_limits = default_resource_limits()
        video_limit = resource_limits[AnalysisResourceClass.VIDEO_DECODE]
        audio_limit = resource_limits[AnalysisResourceClass.AUDIO_DECODE]
        resource_limits[AnalysisResourceClass.VIDEO_DECODE] = ResourcePoolLimit(
            video_decode_workers,
            video_limit.max_pending_tasks,
        )
        resource_limits[AnalysisResourceClass.AUDIO_DECODE] = ResourcePoolLimit(
            audio_decode_workers,
            audio_limit.max_pending_tasks,
        )
        effective_factory = session_factory or MonitoringSessionFactory(
            redis_settings=redis_settings,
            namespace=self.namespace,
            max_concurrent_media_processes=max_concurrent_media_processes,
            per_stream_media_processes=per_stream_media_processes,
            resource_limits=resource_limits,
            service_media_process_gate=self.media_gate,
        )
        self.supervisor = StreamSupervisor(
            session_factory=effective_factory,
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
        self.desired_keys = DesiredStateRedisKeys(self.namespace)
        self.desired_repository = RedisDesiredStateRepository(
            redis_client=self.redis_client,
            keys=self.desired_keys,
        )
        self.reconciler = SupervisorDesiredStateReconciler(
            supervisor=self.supervisor,
            control=self.control,
            repository=self.desired_repository,
        )
        self.command_handler = MonitoringCommandHandler(self.control)
        self.persistent_handler = PersistentMonitoringCommandHandler(
            inner_handler=self.command_handler,
            repository=self.desired_repository,
            supervisor=self.supervisor,
        )
        self.worker_keys = WorkerRedisKeys(self.namespace)
        self.guardrails = CommandGuardrails(
            max_payload_bytes=max_command_payload_bytes,
            max_command_age_seconds=max_command_age_seconds,
        )
        self.command_metrics = CommandMetricsCollector()
        self.command_metrics_publisher = RedisWorkerCommandMetricsPublisher(
            redis_client=self.redis_client,
            keys=self.worker_keys,
            ttl_seconds=command_metrics_ttl,
        )
        self.command_consumer = RedisMonitoringCommandConsumer(
            redis_client=self.redis_client,
            handler=self.persistent_handler,
            keys=ControlRedisKeys(self.namespace),
            consumer_name=consumer_name or f"{self.worker_id}-commands",
            worker_id=self.worker_id,
            guardrails=self.guardrails,
            metrics=self.command_metrics,
            metrics_publisher=self.command_metrics_publisher,
            on_command_finalized=self.projection_service.wake,
        )
        self.heartbeat_publisher = RedisWorkerHeartbeatPublisher(
            redis_client=self.redis_client,
            keys=self.worker_keys,
        )
        self.heartbeat_service = WorkerHeartbeatService(
            worker_id=self.worker_id,
            version=self.worker_version,
            supervisor=self.supervisor,
            media_gate=self.media_gate,
            publisher=self.heartbeat_publisher,
            command_consumer_ready_provider=lambda: self.command_consumer.is_ready,
            command_consumer_required=command_consumer_required,
            heartbeat_interval=heartbeat_interval,
            heartbeat_ttl=heartbeat_ttl,
            discovery_window=worker_discovery_window,
        )
        self._closed = False
        self._redis_closed = False

    def ping(self) -> None:
        self.redis_client.ping()

    def reconcile(self) -> RecoveryReport:
        return self.reconciler.reconcile()

    def run_commands(self, stop_event: Event) -> None:
        while not stop_event.is_set():
            try:
                self.reconciler.reconcile()
                break
            except DesiredStateUnavailableError:
                logger.exception(
                    "Desired state recovery is unavailable; retrying before "
                    "command consumer startup"
                )
                stop_event.wait(self.command_consumer.poll_retry_backoff)
        if stop_event.is_set():
            return
        self.command_consumer.run(stop_event)

    def run_projection(self, stop_event: Event) -> None:
        self.projection_service.run(stop_event)

    def run_heartbeat(self, stop_event: Event) -> None:
        self.heartbeat_service.run(stop_event)

    def stop_streams(self, timeout: float | None = None) -> bool:
        return self.supervisor.stop_all(timeout=timeout)

    def close_redis(self) -> None:
        if self._redis_closed:
            return
        self.redis_client.close()
        self._redis_closed = True

    def close(self, timeout: float | None = None) -> bool:
        if self._closed:
            return True
        stopped = self.stop_streams(timeout=timeout)
        if not stopped:
            return False
        self.close_redis()
        self._closed = True
        return True

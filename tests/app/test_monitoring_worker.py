from app.monitoring_worker import MonitoringWorkerApplication
from app.persistent_monitoring_command_handler import (
    PersistentMonitoringCommandHandler,
)
from app.redis_desired_state_repository import RedisDesiredStateRepository
from app.redis_runtime_status_projector import RedisRuntimeStatusProjector
from app.redis_worker_heartbeat_publisher import RedisWorkerHeartbeatPublisher
from app.runtime_status_projection_service import RuntimeStatusProjectionService
from app.supervisor_desired_state_reconciler import (
    SupervisorDesiredStateReconciler,
)
from app.supervisor_monitoring_control import SupervisorMonitoringControl
from app.supervisor_runtime_status import SupervisorRuntimeStatusReader
from app.worker_heartbeat_service import WorkerHeartbeatService
from core.media_process_budget import ObservableProcessGate
from core.redis_keys import (
    DesiredStateRedisKeys,
    PublicRuntimeRedisKeys,
    RedisNamespace,
    WorkerRedisKeys,
)


def test_worker_composition_shares_one_supervisor_between_ports():
    application = MonitoringWorkerApplication(
        namespace=RedisNamespace("media-monitor:test:composition"),
        max_streams=2,
        worker_id="worker-test-comp",
    )
    try:
        assert isinstance(application.control, SupervisorMonitoringControl)
        assert isinstance(application.runtime_status, SupervisorRuntimeStatusReader)
        assert isinstance(application.public_runtime_keys, PublicRuntimeRedisKeys)
        assert isinstance(application.worker_keys, WorkerRedisKeys)
        assert isinstance(application.desired_keys, DesiredStateRedisKeys)
        assert isinstance(application.desired_repository, RedisDesiredStateRepository)
        assert isinstance(application.projector, RedisRuntimeStatusProjector)
        assert isinstance(application.projection_service, RuntimeStatusProjectionService)
        assert isinstance(application.media_gate, ObservableProcessGate)
        assert isinstance(application.heartbeat_publisher, RedisWorkerHeartbeatPublisher)
        assert isinstance(application.heartbeat_service, WorkerHeartbeatService)
        assert isinstance(application.persistent_handler, PersistentMonitoringCommandHandler)
        assert isinstance(application.reconciler, SupervisorDesiredStateReconciler)
        assert application.worker_id == "worker-test-comp"
        assert application.control.supervisor is application.supervisor
        assert application.runtime_status.supervisor is application.supervisor
        assert application.projection_service.supervisor is application.supervisor
        assert application.projection_service.reader is application.runtime_status
        assert application.projection_service.projector is application.projector
        assert application.heartbeat_service.supervisor is application.supervisor
        assert application.heartbeat_service.media_gate is application.media_gate
        assert application.heartbeat_service.publisher is application.heartbeat_publisher
        assert application.command_handler.control is application.control
        assert application.persistent_handler.inner_handler is application.command_handler
        assert application.persistent_handler.repository is application.desired_repository
        assert application.command_consumer.handler is application.persistent_handler
        assert application.reconciler.supervisor is application.supervisor
        assert application.reconciler.control is application.control
        assert application.reconciler.repository is application.desired_repository
    finally:
        application.close()


def test_worker_close_can_be_retried_when_streams_do_not_drain():
    application = MonitoringWorkerApplication(
        namespace=RedisNamespace("media-monitor:test:close"),
    )
    outcomes = iter((False, True))
    application.supervisor.stop_all = lambda *args, **kwargs: next(outcomes)
    close_calls = []
    application.redis_client.close = lambda: close_calls.append(True)

    assert application.close() is False
    assert close_calls == []
    assert application.close() is True
    assert close_calls == [True]


def test_worker_identity_is_independent_from_redis_consumer_name():
    application = MonitoringWorkerApplication(
        namespace=RedisNamespace("media-monitor:test:identity"),
        worker_id="worker-runtime-01",
        consumer_name="redis-consumer-99",
    )
    try:
        assert application.worker_id == "worker-runtime-01"
        assert application.command_consumer.consumer_name == "redis-consumer-99"
    finally:
        application.close()


def test_generated_worker_identity_does_not_reuse_consumer_name():
    application = MonitoringWorkerApplication(
        namespace=RedisNamespace("media-monitor:test:generated-identity"),
        consumer_name="redis-consumer-01",
    )
    try:
        assert application.worker_id.startswith("worker-")
        assert application.worker_id != "redis-consumer-01"
    finally:
        application.close()


def test_worker_stop_streams_and_close_redis_methods():
    application = MonitoringWorkerApplication(
        namespace=RedisNamespace("media-monitor:test:stop-streams"),
    )
    try:
        assert application.stop_streams(timeout=1.0) is True
        application.close_redis()
    finally:
        application.close()

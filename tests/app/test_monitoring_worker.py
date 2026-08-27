from app.monitoring_worker import MonitoringWorkerApplication
from app.redis_runtime_status_projector import RedisRuntimeStatusProjector
from app.runtime_status_projection_service import RuntimeStatusProjectionService
from app.supervisor_monitoring_control import SupervisorMonitoringControl
from app.supervisor_runtime_status import SupervisorRuntimeStatusReader
from core.redis_keys import PublicRuntimeRedisKeys, RedisNamespace


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
        assert isinstance(application.projector, RedisRuntimeStatusProjector)
        assert isinstance(application.projection_service, RuntimeStatusProjectionService)
        assert application.worker_id == "worker-test-comp"
        assert application.control.supervisor is application.supervisor
        assert application.runtime_status.supervisor is application.supervisor
        assert application.projection_service.supervisor is application.supervisor
        assert application.projection_service.reader is application.runtime_status
        assert application.projection_service.projector is application.projector
        assert application.command_handler.control is application.control
        assert application.command_consumer.handler is application.command_handler
    finally:
        application.close()


def test_worker_close_can_be_retried_when_streams_do_not_drain():
    application = MonitoringWorkerApplication(
        namespace=RedisNamespace("media-monitor:test:close"),
    )
    outcomes = iter((False, True))
    application.supervisor.stop_all = lambda: next(outcomes)
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

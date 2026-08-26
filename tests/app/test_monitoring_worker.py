from app.monitoring_worker import MonitoringWorkerApplication
from app.supervisor_monitoring_control import SupervisorMonitoringControl
from app.supervisor_runtime_status import SupervisorRuntimeStatusReader
from core.redis_keys import RedisNamespace


def test_worker_composition_shares_one_supervisor_between_ports():
    application = MonitoringWorkerApplication(
        namespace=RedisNamespace("media-monitor:test:composition"),
        max_streams=2,
    )
    try:
        assert isinstance(application.control, SupervisorMonitoringControl)
        assert isinstance(application.runtime_status, SupervisorRuntimeStatusReader)
        assert application.control.supervisor is application.supervisor
        assert application.runtime_status.supervisor is application.supervisor
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

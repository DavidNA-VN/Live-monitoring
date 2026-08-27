from threading import Event
import time
import pytest

from app.monitoring_worker_runner import MonitoringWorkerRunner
from core.stream_session import StreamSessionStatus
from core.worker_shutdown import ShutdownReport
from models.worker_heartbeat import WorkerState


class DummyHeartbeatService:
    def __init__(self):
        self.published_states = []

    def publish_heartbeat(self, state: WorkerState):
        self.published_states.append(state)

    def request_stopping(self):
        self.stopping_requested = True

    def run(self, stop_event: Event):
        stop_event.wait()


class DummyProjectionService:
    def __init__(self):
        self.woken = False

    def wake(self):
        self.woken = True

    def run(self, stop_event: Event):
        stop_event.wait()


class DummySupervisor:
    def __init__(self, drain_success=True):
        self.drain_success = drain_success
        self.stopped = False

    def stop_all(self, timeout=None):
        self.stopped = True
        return self.drain_success

    def snapshot(self, stream_id):
        from core.stream_session import StreamSessionSnapshot
        return StreamSessionSnapshot(stream_id=stream_id, status=StreamSessionStatus.RUNNING)


class DummyApplication:
    def __init__(self, drain_success=True):
        self.worker_id = "worker-test-runner"
        self.heartbeat_service = DummyHeartbeatService()
        self.projection_service = DummyProjectionService()
        self.supervisor = DummySupervisor(drain_success=drain_success)
        self.redis_closed = False
        self.commands_run = False
        self.pings = 0

    def ping(self):
        self.pings += 1

    def run_projection(self, stop_event: Event):
        self.projection_service.run(stop_event)

    def run_commands(self, stop_event: Event):
        self.commands_run = True
        stop_event.wait()

    def run_heartbeat(self, stop_event: Event):
        self.heartbeat_service.run(stop_event)

    def stop_streams(self, timeout=None):
        return self.supervisor.stop_all(timeout=timeout)

    def close_redis(self):
        self.redis_closed = True


def test_runner_clean_shutdown():
    app = DummyApplication()
    runner = MonitoringWorkerRunner(
        application=app,
        command_worker=True,
        shutdown_timeout=5.0,
    )

    # Trigger shutdown asynchronously
    def _trigger_shutdown():
        time.sleep(0.05)
        runner.request_shutdown()

    import threading
    t = threading.Thread(target=_trigger_shutdown)
    t.start()

    exit_code = runner.run(install_signal_handlers=False)
    t.join()

    assert exit_code == 0
    report = runner.shutdown()
    assert report.commands_drained is True
    assert report.heartbeat_stopped is True
    assert report.projection_stopped is True
    assert report.streams_drained is True
    assert report.redis_closed is True
    assert report.timed_out is False
    assert report.errors == ()
    assert app.redis_closed is True
    assert app.projection_service.woken is True
    assert WorkerState.STOPPING in app.heartbeat_service.published_states


def test_runner_thread_failure_propagation():
    app = DummyApplication()

    def failing_commands(stop_event: Event):
        raise RuntimeError("Fatal command consumer programming bug")

    app.run_commands = failing_commands

    runner = MonitoringWorkerRunner(
        application=app,
        command_worker=True,
        shutdown_timeout=5.0,
    )

    exit_code = runner.run(install_signal_handlers=False)
    assert exit_code == 1

    report = runner.shutdown()
    assert any("command_consumer: Fatal command consumer programming bug" in err for err in report.errors)
    assert report.redis_closed is True


def test_runner_stream_drain_failure_reported():
    app = DummyApplication(drain_success=False)
    runner = MonitoringWorkerRunner(
        application=app,
        command_worker=False,
        shutdown_timeout=5.0,
    )

    # Trigger shutdown
    runner.request_shutdown()
    exit_code = runner.run(install_signal_handlers=False)
    assert exit_code == 1

    report = runner.shutdown()
    assert report.streams_drained is False
    assert report.redis_closed is False
    assert app.redis_closed is False
    assert any("supervisor: stream sessions failed to drain" in err for err in report.errors)


def test_runner_retries_incomplete_shutdown_before_closing_redis():
    app = DummyApplication(drain_success=False)
    runner = MonitoringWorkerRunner(
        application=app,
        shutdown_timeout=5.0,
    )

    first = runner.shutdown()
    assert first.redis_closed is False
    app.supervisor.drain_success = True

    second = runner.shutdown()
    assert second.streams_drained is True
    assert second.redis_closed is True
    assert app.redis_closed is True


def test_runner_rejects_non_positive_shutdown_timeout():
    with pytest.raises(ValueError, match="shutdown_timeout"):
        MonitoringWorkerRunner(
            application=DummyApplication(),
            shutdown_timeout=0,
        )


def test_runner_keeps_redis_open_while_command_thread_is_alive():
    app = DummyApplication()

    def slow_commands(_stop_event: Event):
        time.sleep(0.15)

    app.run_commands = slow_commands
    runner = MonitoringWorkerRunner(
        application=app,
        command_worker=True,
        shutdown_timeout=0.02,
    )
    runner._start_thread(
        "command_consumer",
        app.run_commands,
        runner._command_stop_event,
    )

    first = runner.shutdown()
    assert first.commands_drained is False
    assert first.redis_closed is False
    assert app.redis_closed is False

    runner._threads["command_consumer"].join(timeout=1.0)
    second = runner.shutdown()
    assert second.commands_drained is True
    assert second.redis_closed is True

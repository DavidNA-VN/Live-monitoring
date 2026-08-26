from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from app.supervisor_monitoring_control import SupervisorMonitoringControl
from core.monitoring_control import (
    MonitoringAction,
    StreamAlreadyExistsError,
    StreamCapacityError,
    StreamIdentityMismatchError,
    StreamNotFoundError,
    StreamOperationError,
)
from core.stream_session import (
    StreamSessionSnapshot,
    StreamSessionStatus,
)
from core.stream_supervisor import StreamSupervisor
from models.analysis import AnalysisResourceClass, ResourcePoolLimit
from models.stream_config import StreamConfig


def make_config(name: str, *, enabled: bool = True, workers: int = 1) -> StreamConfig:
    return StreamConfig(
        master_url=f"https://test/{name}/master.m3u8",
        stream_id=name,
        enabled=enabled,
        resource_limits={
            AnalysisResourceClass.VIDEO_DECODE: ResourcePoolLimit(workers, 0)
        },
    )


class StubSession:
    def __init__(self, config: StreamConfig, *, stop_result: bool = True):
        self.config = config
        self.stream_id = config.identity.external_stream_id
        self.started = False
        self.stop_result = stop_result
        self.stop_calls = 0
        self.status = StreamSessionStatus.CREATED

    def start(self):
        self.started = True
        self.status = StreamSessionStatus.RUNNING

    def stop(self, *, timeout=None, paused=False):
        self.stop_calls += 1
        if self.stop_result:
            self.status = (
                StreamSessionStatus.PAUSED if paused else StreamSessionStatus.STOPPED
            )
        return self.stop_result

    def snapshot(self):
        return StreamSessionSnapshot(self.stream_id, self.status)


class StubFactory:
    def __init__(self):
        self.created: list[StubSession] = []
        self.stop_results: list[bool] = []

    def create(self, config: StreamConfig) -> StubSession:
        result = self.stop_results.pop(0) if self.stop_results else True
        session = StubSession(config, stop_result=result)
        self.created.append(session)
        return session


class FailingFactory:
    def create(self, _config: StreamConfig):
        raise RuntimeError("session assembly failed")


class ValueErrorFactory:
    def create(self, _config: StreamConfig):
        raise ValueError("invalid session assembly")


class SlowFactory(StubFactory):
    def __init__(self):
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def create(self, config: StreamConfig) -> StubSession:
        self.entered.set()
        assert self.release.wait(timeout=2.0)
        return super().create(config)


def test_start_absent_stream():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    result = control.start(cfg)

    assert result.stream_id == "channel-01"
    assert result.action is MonitoringAction.START
    assert result.changed is True
    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.RUNNING
    assert len(factory.created) == 1


def test_start_duplicate_same_config_when_running():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    first_res = control.start(cfg)
    assert first_res.changed is True
    assert len(factory.created) == 1

    second_res = control.start(cfg)
    assert second_res.changed is False
    assert second_res.action is MonitoringAction.START
    assert len(factory.created) == 1


def test_start_conflict_different_config():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg1 = make_config("channel-01", workers=1)
    cfg2 = make_config("channel-01", workers=2)

    control.start(cfg1)
    with pytest.raises(StreamAlreadyExistsError):
        control.start(cfg2)

    assert len(factory.created) == 1
    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.RUNNING


def test_start_on_paused_or_stopped_resumes_stream():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    control.start(cfg)
    control.pause("channel-01")
    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.PAUSED

    result = control.start(cfg)
    assert result.changed is True
    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.RUNNING
    assert len(factory.created) == 2


def test_start_on_failed_stream_recovers_and_restarts():
    supervisor = StreamSupervisor(session_factory=FailingFactory())
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    with pytest.raises(StreamOperationError):
        control.start(cfg)

    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.FAILED

    # Swap to working factory and start again with same config
    supervisor.session_factory = StubFactory()
    result = control.start(cfg)
    assert result.changed is True
    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.RUNNING


def test_start_translates_factory_value_error_as_operation_failure():
    supervisor = StreamSupervisor(session_factory=ValueErrorFactory())
    control = SupervisorMonitoringControl(supervisor)

    with pytest.raises(StreamOperationError) as error:
        control.start(make_config("channel-01"))

    assert isinstance(error.value.__cause__, ValueError)


def test_start_rejects_disabled_config_without_registering_stream():
    supervisor = StreamSupervisor(session_factory=StubFactory())
    control = SupervisorMonitoringControl(supervisor)

    with pytest.raises(StreamOperationError, match="disabled stream"):
        control.start(make_config("channel-01", enabled=False))

    assert supervisor.snapshot("channel-01") is None


def test_pause_idempotency_and_lifecycle():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    control.start(cfg)

    # RUNNING -> PAUSED: changed=True
    res1 = control.pause("channel-01")
    assert res1.changed is True
    assert res1.action is MonitoringAction.PAUSE
    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.PAUSED

    # PAUSED -> PAUSED: changed=False
    res2 = control.pause("channel-01")
    assert res2.changed is False

    # Pause unknown stream -> StreamNotFoundError
    with pytest.raises(StreamNotFoundError):
        control.pause("unknown-channel")


def test_resume_idempotency_and_lifecycle():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    control.start(cfg)
    control.pause("channel-01")

    # PAUSED -> RUNNING: changed=True
    res1 = control.resume("channel-01")
    assert res1.changed is True
    assert res1.action is MonitoringAction.RESUME
    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.RUNNING

    # RUNNING -> RUNNING: changed=False
    res2 = control.resume("channel-01")
    assert res2.changed is False

    # Resume unknown stream -> StreamNotFoundError
    with pytest.raises(StreamNotFoundError):
        control.resume("unknown-channel")


def test_resume_on_failed_stream_raises_explicit_operation_error():
    supervisor = StreamSupervisor(session_factory=FailingFactory())
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    with pytest.raises(StreamOperationError):
        control.start(cfg)

    with pytest.raises(StreamOperationError, match="Failed stream must be started again"):
        control.resume("channel-01")


def test_stop_idempotency_and_lifecycle():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    control.start(cfg)

    # existing -> removed: changed=True
    res1 = control.stop("channel-01")
    assert res1.changed is True
    assert res1.action is MonitoringAction.STOP
    assert supervisor.snapshot("channel-01") is None

    # missing -> no-op: changed=False
    res2 = control.stop("channel-01")
    assert res2.changed is False


def test_stop_drain_timeout_raises_operation_error():
    factory = StubFactory()
    factory.stop_results = [False]
    supervisor = StreamSupervisor(
        session_factory=factory,
        shutdown_timeout=0.01,
    )
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    control.start(cfg)

    with pytest.raises(StreamOperationError, match="Stream did not drain"):
        control.stop("channel-01")

    assert supervisor.snapshot("channel-01") is not None


def test_update_config_same_config_is_noop():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01", workers=1)
    control.start(cfg)
    assert len(factory.created) == 1

    # Same config update -> changed=False, no restart
    result = control.update_config("channel-01", cfg)
    assert result.changed is False
    assert result.action is MonitoringAction.UPDATE_CONFIG
    assert len(factory.created) == 1


def test_update_config_changed_config_restarts_running_stream():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg1 = make_config("channel-01", workers=1)
    cfg2 = make_config("channel-01", workers=2)
    control.start(cfg1)
    assert len(factory.created) == 1

    result = control.update_config("channel-01", cfg2)
    assert result.changed is True
    assert len(factory.created) == 2
    assert supervisor.configuration("channel-01") == cfg2


def test_update_config_changed_config_preserves_paused_state():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg1 = make_config("channel-01", workers=1)
    cfg2 = make_config("channel-01", workers=2)
    control.start(cfg1)
    control.pause("channel-01")
    assert len(factory.created) == 1

    result = control.update_config("channel-01", cfg2)
    assert result.changed is True
    assert len(factory.created) == 1
    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.PAUSED
    assert supervisor.configuration("channel-01") == cfg2


def test_update_config_identity_mismatch():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    with pytest.raises(StreamIdentityMismatchError):
        control.update_config("channel-02", cfg)


def test_update_config_stream_not_found():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    control = SupervisorMonitoringControl(supervisor)

    cfg = make_config("channel-01")
    with pytest.raises(StreamNotFoundError):
        control.update_config("channel-01", cfg)


def test_capacity_enforcement():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory, max_streams=1)
    control = SupervisorMonitoringControl(supervisor)

    control.start(make_config("channel-01"))
    with pytest.raises(StreamCapacityError):
        control.start(make_config("channel-02"))


def test_concurrency_start_same_config_allocates_single_session():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory, max_streams=2)
    control = SupervisorMonitoringControl(supervisor)
    cfg = make_config("channel-01")

    barrier = Barrier(2)

    def worker():
        barrier.wait()
        return control.start(cfg)

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker)
        f2 = executor.submit(worker)
        r1 = f1.result()
        r2 = f2.result()

    results = [r1, r2]
    changed_count = sum(1 for r in results if r.changed is True)
    unchanged_count = sum(1 for r in results if r.changed is False)

    assert changed_count == 1
    assert unchanged_count == 1
    assert len(factory.created) == 1
    assert supervisor.snapshot("channel-01").status is StreamSessionStatus.RUNNING


def test_slow_concurrent_start_never_allocates_duplicate_session():
    factory = SlowFactory()
    supervisor = StreamSupervisor(session_factory=factory, max_streams=2)
    control = SupervisorMonitoringControl(supervisor)
    cfg = make_config("channel-01")
    barrier = Barrier(2)

    def worker():
        barrier.wait()
        return control.start(cfg)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(worker)
        second = executor.submit(worker)
        assert factory.entered.wait(timeout=1.0)
        factory.release.set()
        results = (first.result(), second.result())

    assert sorted(item.changed for item in results) == [False, True]
    assert len(factory.created) == 1

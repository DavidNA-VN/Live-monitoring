from datetime import datetime, timezone
import pytest

from app.supervisor_desired_state_reconciler import (
    SupervisorDesiredStateReconciler,
)
from core.desired_state_repository import DesiredStateRepository
from core.monitoring_control import MonitoringControl, MonitoringControlResult, MonitoringAction
from core.stream_session import StreamSessionStatus
from core.stream_supervisor import StreamSupervisor
from models.desired_stream_state import (
    DesiredLifecycleState,
    DesiredStreamState,
)
from models.stream_config import StreamConfig


class FakeSessionFactory:
    def __init__(self):
        self.created = []

    def create(self, config):
        self.created.append(config.identity.external_stream_id)
        return DummySession(config)


class DummySession:
    def __init__(self, config):
        self.config = config
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self, timeout=30.0, paused=False):
        self.stopped = True
        return True

    def snapshot(self):
        from core.stream_session import StreamSessionSnapshot
        return StreamSessionSnapshot(
            stream_id=self.config.identity.external_stream_id,
            status=StreamSessionStatus.RUNNING if self.started else StreamSessionStatus.CREATED,
        )


from app.supervisor_monitoring_control import SupervisorMonitoringControl


class InMemoryDesiredStateRepo(DesiredStateRepository):
    def __init__(self, records=None):
        self.records = records or []

    def get(self, stream_id):
        for r in self.records:
            if r.stream_id == stream_id:
                return r
        return None

    def list_all(self):
        return self.records

    def save(self, record):
        self.records.append(record)

    def delete(self, stream_id):
        return True

    def quarantine(self, stream_id, raw_payload, error):
        pass


def make_config(stream_id="channel-01"):
    return StreamConfig(
        stream_id=stream_id,
        master_url="https://example.test/live.m3u8",
        black_screen_enabled=True,
        audio_loss_enabled=False,
    )


def test_reconciler_restores_running_and_paused_streams():
    factory = FakeSessionFactory()
    supervisor = StreamSupervisor(session_factory=factory, max_streams=10)
    control = SupervisorMonitoringControl(supervisor)

    now = datetime.now(timezone.utc)
    records = [
        DesiredStreamState(
            stream_id="stream-running",
            desired_state=DesiredLifecycleState.RUNNING,
            updated_at=now,
            config=make_config("stream-running"),
        ),
        DesiredStreamState(
            stream_id="stream-paused",
            desired_state=DesiredLifecycleState.PAUSED,
            updated_at=now,
            config=make_config("stream-paused"),
        ),
        DesiredStreamState(
            stream_id="stream-stopped",
            desired_state=DesiredLifecycleState.STOPPED,
            updated_at=now,
            config=None,
        ),
    ]

    repo = InMemoryDesiredStateRepo(records)
    reconciler = SupervisorDesiredStateReconciler(
        supervisor=supervisor,
        control=control,
        repository=repo,
    )

    report = reconciler.reconcile()
    assert report.total_records == 3
    assert report.running_started == 1
    assert report.paused_registered == 1
    assert report.stopped_skipped == 1
    assert len(report.errors) == 0

    # 1. RUNNING stream session created & started
    assert "stream-running" in factory.created
    assert supervisor.snapshot("stream-running").status == StreamSessionStatus.RUNNING

    # 2. PAUSED stream slot exists with PAUSED status, but NO session created (never started!)
    assert "stream-paused" not in factory.created
    assert supervisor.snapshot("stream-paused").status == StreamSessionStatus.PAUSED

    # 3. STOPPED stream slot does NOT exist in supervisor
    assert supervisor.snapshot("stream-stopped") is None


def test_reconciler_failure_isolation():
    factory = FakeSessionFactory()
    supervisor = StreamSupervisor(session_factory=factory, max_streams=1)  # Capacity 1
    control = SupervisorMonitoringControl(supervisor)

    now = datetime.now(timezone.utc)
    records = [
        DesiredStreamState(
            stream_id="stream-1",
            desired_state=DesiredLifecycleState.RUNNING,
            updated_at=now,
            config=make_config("stream-1"),
        ),
        DesiredStreamState(
            stream_id="stream-2",
            desired_state=DesiredLifecycleState.RUNNING,
            updated_at=now,
            config=make_config("stream-2"),
        ),
    ]

    repo = InMemoryDesiredStateRepo(records)
    reconciler = SupervisorDesiredStateReconciler(
        supervisor=supervisor,
        control=control,
        repository=repo,
    )

    report = reconciler.reconcile()
    assert report.running_started == 1
    assert "stream-2" in report.errors  # Capacity error recorded without crash!
    assert supervisor.snapshot("stream-1").status == StreamSessionStatus.RUNNING


def test_reconciler_is_idempotent():
    factory = FakeSessionFactory()
    supervisor = StreamSupervisor(session_factory=factory, max_streams=5)
    control = SupervisorMonitoringControl(supervisor)

    now = datetime.now(timezone.utc)
    records = [
        DesiredStreamState(
            stream_id="stream-running",
            desired_state=DesiredLifecycleState.RUNNING,
            updated_at=now,
            config=make_config("stream-running"),
        ),
        DesiredStreamState(
            stream_id="stream-paused",
            desired_state=DesiredLifecycleState.PAUSED,
            updated_at=now,
            config=make_config("stream-paused"),
        ),
    ]

    repo = InMemoryDesiredStateRepo(records)
    reconciler = SupervisorDesiredStateReconciler(
        supervisor=supervisor,
        control=control,
        repository=repo,
    )

    # First run
    reconciler.reconcile()
    # Second run
    report2 = reconciler.reconcile()
    assert len(report2.errors) == 0
    assert supervisor.snapshot("stream-running").status == StreamSessionStatus.RUNNING
    assert supervisor.snapshot("stream-paused").status == StreamSessionStatus.PAUSED

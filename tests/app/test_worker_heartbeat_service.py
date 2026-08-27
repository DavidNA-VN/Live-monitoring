from datetime import datetime, timezone
from threading import Event, Thread
import time
import pytest

from app.worker_heartbeat_service import WorkerHeartbeatService
from core.media_process_budget import ObservableProcessGate
from core.worker_heartbeat import (
    WorkerHeartbeatPublisher,
    WorkerHeartbeatPublishError,
)
from models.worker_heartbeat import WorkerHeartbeat, WorkerState


class DummyPublisher(WorkerHeartbeatPublisher):
    def __init__(self):
        self.published: list[WorkerHeartbeat] = []
        self.fail: bool = False

    def publish(self, heartbeat: WorkerHeartbeat, *, ttl_seconds: int = 15, discovery_window_seconds: int = 30) -> None:
        if self.fail:
            raise WorkerHeartbeatPublishError("Simulated failure")
        self.published.append(heartbeat)


class DummySupervisor:
    def __init__(self, max_streams: int = 16):
        self.max_streams = max_streams
        self._count = 0

    def stream_count(self) -> int:
        return self._count


def test_service_lifecycle_states_and_readiness_transitions():
    supervisor = DummySupervisor(max_streams=8)
    media_gate = ObservableProcessGate(max_concurrent=4)
    publisher = DummyPublisher()
    consumer_ready = False

    service = WorkerHeartbeatService(
        worker_id="worker-local-01",
        version="v1.0.0",
        supervisor=supervisor,
        media_gate=media_gate,
        publisher=publisher,
        command_consumer_ready_provider=lambda: consumer_ready,
        command_consumer_required=True,
    )

    # 1. Initial build: consumer not ready yet -> STARTING
    assert service.publish_heartbeat() is True
    hb1 = publisher.published[-1]
    assert hb1.state == WorkerState.STARTING
    assert hb1.command_consumer_ready is False

    # 2. Consumer becomes ready -> READY
    consumer_ready = True
    assert service.publish_heartbeat() is True
    hb2 = publisher.published[-1]
    assert hb2.state == WorkerState.READY
    assert hb2.command_consumer_ready is True

    # 3. Consumer loses readiness -> DEGRADED
    consumer_ready = False
    assert service.publish_heartbeat() is True
    hb3 = publisher.published[-1]
    assert hb3.state == WorkerState.DEGRADED
    assert hb3.command_consumer_ready is False


def test_service_direct_url_mode_is_ready_without_consumer():
    supervisor = DummySupervisor(max_streams=4)
    media_gate = ObservableProcessGate(max_concurrent=2)
    publisher = DummyPublisher()

    service = WorkerHeartbeatService(
        worker_id="worker-url-01",
        supervisor=supervisor,
        media_gate=media_gate,
        publisher=publisher,
        command_consumer_ready_provider=lambda: False,
        command_consumer_required=False,  # direct URL mode
    )

    hb = service.build_heartbeat()
    assert hb.state == WorkerState.READY
    assert hb.command_consumer_ready is False


def test_service_reads_actual_capacity():
    supervisor = DummySupervisor(max_streams=10)
    supervisor._count = 3
    media_gate = ObservableProcessGate(max_concurrent=6)
    media_gate.acquire()
    media_gate.acquire()

    publisher = DummyPublisher()
    service = WorkerHeartbeatService(
        worker_id="worker-cap-01",
        supervisor=supervisor,
        media_gate=media_gate,
        publisher=publisher,
        command_consumer_required=False,
    )

    hb = service.build_heartbeat()
    assert hb.active_stream_count == 3
    assert hb.max_streams == 10
    assert hb.active_media_processes == 2
    assert hb.max_media_processes == 6


def test_service_publishes_stopping_on_shutdown():
    supervisor = DummySupervisor()
    media_gate = ObservableProcessGate(max_concurrent=2)
    publisher = DummyPublisher()

    service = WorkerHeartbeatService(
        worker_id="worker-stop-01",
        supervisor=supervisor,
        media_gate=media_gate,
        publisher=publisher,
        heartbeat_interval=10.0,
        command_consumer_required=False,
    )

    stop_event = Event()
    thread = Thread(target=service.run, args=(stop_event,), daemon=True)
    thread.start()

    time.sleep(0.05)
    stop_event.set()
    thread.join(timeout=1.0)

    assert len(publisher.published) >= 2
    # First is STARTING
    assert publisher.published[0].state == WorkerState.STARTING
    # Last is STOPPING
    assert publisher.published[-1].state == WorkerState.STOPPING


def test_service_handles_publish_failure_gracefully():
    supervisor = DummySupervisor()
    media_gate = ObservableProcessGate(max_concurrent=2)
    publisher = DummyPublisher()
    publisher.fail = True

    service = WorkerHeartbeatService(
        worker_id="worker-fail-01",
        supervisor=supervisor,
        media_gate=media_gate,
        publisher=publisher,
        command_consumer_required=False,
    )

    assert service.publish_heartbeat() is False


def test_failed_ready_publish_does_not_make_next_state_degraded():
    supervisor = DummySupervisor()
    media_gate = ObservableProcessGate(max_concurrent=2)
    publisher = DummyPublisher()
    consumer_ready = True
    service = WorkerHeartbeatService(
        worker_id="worker-state-01",
        supervisor=supervisor,
        media_gate=media_gate,
        publisher=publisher,
        command_consumer_ready_provider=lambda: consumer_ready,
        command_consumer_required=True,
    )

    publisher.fail = True
    assert service.publish_heartbeat() is False
    assert service.state == WorkerState.STARTING

    consumer_ready = False
    publisher.fail = False
    assert service.publish_heartbeat() is True
    assert publisher.published[-1].state == WorkerState.STARTING


def test_programming_error_from_readiness_provider_is_not_swallowed():
    def broken_provider():
        raise RuntimeError("provider bug")

    service = WorkerHeartbeatService(
        worker_id="worker-bug-01",
        supervisor=DummySupervisor(),
        media_gate=ObservableProcessGate(max_concurrent=1),
        publisher=DummyPublisher(),
        command_consumer_ready_provider=broken_provider,
    )

    with pytest.raises(RuntimeError, match="provider bug"):
        service.publish_heartbeat()

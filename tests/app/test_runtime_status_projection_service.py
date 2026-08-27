from datetime import datetime, timezone
from threading import Event, Thread
import time
import pytest

from app.runtime_status_projection_service import RuntimeStatusProjectionService
from core.runtime_status_projector import RuntimeStatusProjector
from core.runtime_status_reader import RuntimeStatusReader
from models.runtime_status import (
    CheckStatus,
    PublicStreamStatus,
    RuntimeHealth,
    RuntimeStatus,
)


class DummyProjector(RuntimeStatusProjector):
    def __init__(self):
        self.projected: list[RuntimeStatus] = []
        self.removed: list[dict] = []
        self.fail_streams: set[str] = set()
        self.fail_removals: set[str] = set()

    def project(self, status: RuntimeStatus) -> bool:
        if status.stream_id in self.fail_streams:
            from app.redis_runtime_status_projector import RuntimeStatusProjectionError
            raise RuntimeStatusProjectionError(f"Fail on {status.stream_id}")
        self.projected.append(status)
        return True

    def remove(self, *, stream_id: str, worker_id: str, observed_at: datetime) -> bool:
        if stream_id in self.fail_removals:
            from app.redis_runtime_status_projector import RuntimeStatusProjectionError
            raise RuntimeStatusProjectionError(f"Fail remove on {stream_id}")
        self.removed.append({
            "stream_id": stream_id,
            "worker_id": worker_id,
            "observed_at": observed_at,
        })
        return True


class DummyReader(RuntimeStatusReader):
    def __init__(self):
        self.statuses: dict[str, RuntimeStatus] = {}

    def get(self, stream_id: str) -> RuntimeStatus | None:
        return self.statuses.get(stream_id)


class DummySupervisor:
    def __init__(self):
        self._snapshots: dict[str, object] = {}

    def snapshots(self) -> dict[str, object]:
        return dict(self._snapshots)


def test_service_projects_all_active_streams():
    supervisor = DummySupervisor()
    reader = DummyReader()
    projector = DummyProjector()

    supervisor._snapshots["stream-1"] = object()
    supervisor._snapshots["stream-2"] = object()

    reader.statuses["stream-1"] = RuntimeStatus(
        stream_id="stream-1",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
    )
    reader.statuses["stream-2"] = RuntimeStatus(
        stream_id="stream-2",
        status=PublicStreamStatus.PAUSED,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
    )

    fixed_now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    service = RuntimeStatusProjectionService(
        worker_id="worker-local-01",
        supervisor=supervisor,
        reader=reader,
        projector=projector,
        clock=lambda: fixed_now,
    )

    service.project_once()

    assert len(projector.projected) == 2
    for p in projector.projected:
        assert p.worker_id == "worker-local-01"
        assert p.observed_at == fixed_now


def test_service_detects_removed_streams_and_calls_remove():
    supervisor = DummySupervisor()
    reader = DummyReader()
    projector = DummyProjector()

    supervisor._snapshots["stream-1"] = object()
    reader.statuses["stream-1"] = RuntimeStatus(
        stream_id="stream-1",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
    )

    service = RuntimeStatusProjectionService(
        worker_id="worker-local-01",
        supervisor=supervisor,
        reader=reader,
        projector=projector,
    )

    # First cycle: knows stream-1
    service.project_once()
    assert len(projector.projected) == 1
    assert len(projector.removed) == 0

    # Stream is removed from supervisor
    del supervisor._snapshots["stream-1"]
    del reader.statuses["stream-1"]

    # Second cycle: detects stream-1 removed
    service.project_once()
    assert len(projector.removed) == 1
    assert projector.removed[0]["stream_id"] == "stream-1"
    assert projector.removed[0]["worker_id"] == "worker-local-01"


def test_service_failure_on_one_stream_does_not_block_others():
    supervisor = DummySupervisor()
    reader = DummyReader()
    projector = DummyProjector()

    supervisor._snapshots["stream-fail"] = object()
    supervisor._snapshots["stream-ok"] = object()
    projector.fail_streams.add("stream-fail")

    reader.statuses["stream-fail"] = RuntimeStatus(
        stream_id="stream-fail",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
    )
    reader.statuses["stream-ok"] = RuntimeStatus(
        stream_id="stream-ok",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
    )

    service = RuntimeStatusProjectionService(
        worker_id="worker-local-01",
        supervisor=supervisor,
        reader=reader,
        projector=projector,
    )

    service.project_once()

    # stream-ok was still projected successfully
    assert any(p.stream_id == "stream-ok" for p in projector.projected)


def test_failed_removal_is_retried_on_next_cycle():
    supervisor = DummySupervisor()
    reader = DummyReader()
    projector = DummyProjector()
    supervisor._snapshots["stream-1"] = object()
    reader.statuses["stream-1"] = RuntimeStatus(
        stream_id="stream-1",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
    )
    service = RuntimeStatusProjectionService(
        worker_id="worker-local-01",
        supervisor=supervisor,
        reader=reader,
        projector=projector,
    )
    service.project_once()
    del supervisor._snapshots["stream-1"]
    projector.fail_removals.add("stream-1")

    service.project_once()
    assert projector.removed == []

    projector.fail_removals.clear()
    service.project_once()
    assert projector.removed[0]["stream_id"] == "stream-1"


def test_service_wake_and_run_lifecycle():
    supervisor = DummySupervisor()
    reader = DummyReader()
    projector = DummyProjector()

    service = RuntimeStatusProjectionService(
        worker_id="worker-local-01",
        supervisor=supervisor,
        reader=reader,
        projector=projector,
        projection_interval=10.0,  # long sleep
    )

    stop_event = Event()
    thread = Thread(target=service.run, args=(stop_event,), daemon=True)
    thread.start()

    time.sleep(0.05)
    # Add stream and wake
    supervisor._snapshots["stream-wake"] = object()
    reader.statuses["stream-wake"] = RuntimeStatus(
        stream_id="stream-wake",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
    )

    service.wake()
    time.sleep(0.1)

    assert any(p.stream_id == "stream-wake" for p in projector.projected)

    stop_event.set()
    service.wake()
    thread.join(timeout=1.0)
    assert not thread.is_alive()


def test_shutdown_runs_final_snapshot_cycle_without_removing_stream():
    supervisor = DummySupervisor()
    reader = DummyReader()
    projector = DummyProjector()
    supervisor._snapshots["stream-final"] = object()
    reader.statuses["stream-final"] = RuntimeStatus(
        stream_id="stream-final",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
    )
    service = RuntimeStatusProjectionService(
        worker_id="worker-local-01",
        supervisor=supervisor,
        reader=reader,
        projector=projector,
        projection_interval=10.0,
    )
    stop_event = Event()
    thread = Thread(target=service.run, args=(stop_event,), daemon=True)
    thread.start()
    time.sleep(0.05)
    reader.statuses["stream-final"] = RuntimeStatus(
        stream_id="stream-final",
        status=PublicStreamStatus.FAILED,
        health=RuntimeHealth.UNHEALTHY,
        active_variant_count=0,
        queue_depth=0,
        checks={},
        error="runtime failed",
    )

    stop_event.set()
    service.wake()
    thread.join(timeout=1.0)

    assert projector.projected[-1].status is PublicStreamStatus.FAILED
    assert projector.removed == []

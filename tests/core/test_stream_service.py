from threading import Event

import pytest

from core.stream_session import (
    StreamSession,
    StreamSessionSnapshot,
    StreamSessionStatus,
)
from core.stream_supervisor import StreamSupervisor
from models.stream_config import StreamConfig


class BlockingRuntime:
    def __init__(self):
        self.stop_requested = Event()
        self.allow_drain = Event()

    def run_forever(self):
        self.stop_requested.wait()
        self.allow_drain.wait()

    def stop(self):
        self.stop_requested.set()


def config(name, *, enabled=True, workers=1):
    return StreamConfig(
        master_url=f"https://test/{name}/master.m3u8",
        stream_id=name,
        enabled=enabled,
        max_decode_workers=workers,
    )


def test_session_reports_stopping_until_inflight_drain_completes():
    runtime = BlockingRuntime()
    closed = []
    session = StreamSession(
        config=config("one"),
        runtime=runtime,
        close_callbacks=(lambda: closed.append(True),),
    )
    session.start()

    assert session.stop(timeout=0.01) is False
    assert session.snapshot().status == StreamSessionStatus.STOPPING
    assert closed == []

    runtime.allow_drain.set()
    assert session.stop(timeout=1.0) is True
    assert session.snapshot().status == StreamSessionStatus.STOPPED
    assert closed == [True]


class StubSession:
    def __init__(self, item, *, stop_result=True):
        self.config = item
        self.stream_id = item.identity.stream_id
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
                StreamSessionStatus.PAUSED
                if paused
                else StreamSessionStatus.STOPPED
            )
        return self.stop_result

    def snapshot(self):
        return StreamSessionSnapshot(self.stream_id, self.status)


class StubFactory:
    def __init__(self):
        self.created = []
        self.stop_results = []

    def create(self, item):
        result = (
            self.stop_results.pop(0) if self.stop_results else True
        )
        session = StubSession(item, stop_result=result)
        self.created.append(session)
        return session


class FailingFactory:
    def create(self, _item):
        raise RuntimeError("assembly failed")


def test_supervisor_add_pause_resume_update_remove_lifecycle():
    factory = StubFactory()
    supervisor = StreamSupervisor(
        session_factory=factory,
        max_streams=2,
        shutdown_timeout=0.1,
    )
    first = config("one")
    stream_id = supervisor.add(first)
    assert supervisor.snapshots()[stream_id].status == (
        StreamSessionStatus.RUNNING
    )
    assert supervisor.is_healthy() is True

    assert supervisor.pause(stream_id) is True
    assert supervisor.snapshots()[stream_id].status == (
        StreamSessionStatus.PAUSED
    )
    assert supervisor.is_healthy() is True
    supervisor.resume(stream_id)
    assert len(factory.created) == 2

    supervisor.update(stream_id, config("one", workers=2))
    assert len(factory.created) == 3
    assert factory.created[-1].config.max_decode_workers == 2
    assert supervisor.remove(stream_id) is True
    assert supervisor.snapshots() == {}


def test_supervisor_enforces_per_worker_stream_capacity():
    supervisor = StreamSupervisor(
        session_factory=StubFactory(), max_streams=1
    )
    supervisor.add(config("one"), start=False)

    with pytest.raises(RuntimeError, match="capacity"):
        supervisor.add(config("two"), start=False)


def test_supervisor_exposes_session_assembly_failure():
    supervisor = StreamSupervisor(session_factory=FailingFactory())
    item = config("one")

    with pytest.raises(RuntimeError, match="assembly failed"):
        supervisor.add(item)

    snapshot = supervisor.snapshots()[item.identity.stream_id]
    assert snapshot.status == StreamSessionStatus.FAILED
    assert snapshot.error == "assembly failed"
    assert supervisor.is_healthy() is False


def test_start_all_activates_registered_enabled_streams():
    factory = StubFactory()
    supervisor = StreamSupervisor(
        session_factory=factory, max_streams=2
    )
    first = supervisor.add(config("one"), start=False)
    second = supervisor.add(config("two"), start=False)

    assert supervisor.start_all() == {}
    assert set(supervisor.snapshots()) == {first, second}
    assert all(
        item.status == StreamSessionStatus.RUNNING
        for item in supervisor.snapshots().values()
    )


def test_update_preserves_paused_operational_state():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)
    stream_id = supervisor.add(config("one"))
    supervisor.pause(stream_id)

    supervisor.update(stream_id, config("one", workers=2))

    assert len(factory.created) == 1
    assert supervisor.snapshots()[stream_id].status == (
        StreamSessionStatus.PAUSED
    )
    supervisor.resume(stream_id)
    assert factory.created[-1].config.max_decode_workers == 2


def test_stop_all_attempts_every_session_when_one_times_out():
    factory = StubFactory()
    factory.stop_results = [False, True]
    supervisor = StreamSupervisor(
        session_factory=factory,
        max_streams=2,
        shutdown_timeout=0.01,
    )
    supervisor.add(config("one"))
    supervisor.add(config("two"))

    assert supervisor.stop_all() is False
    assert [item.stop_calls for item in factory.created] == [1, 1]
    assert supervisor.is_healthy() is True


def test_disabled_config_is_registered_without_allocating_session():
    factory = StubFactory()
    supervisor = StreamSupervisor(session_factory=factory)

    stream_id = supervisor.add(config("one", enabled=False))

    assert factory.created == []
    assert supervisor.snapshots()[stream_id].status == (
        StreamSessionStatus.PAUSED
    )
    assert supervisor.is_healthy() is True

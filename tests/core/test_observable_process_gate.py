from concurrent.futures import ThreadPoolExecutor
import threading
import pytest

from core.media_process_budget import (
    CompositeProcessGate,
    ObservableProcessGate,
    process_gate,
)


def test_gate_acquire_release_updates_snapshot_counter():
    gate = ObservableProcessGate(max_concurrent=3)
    snap = gate.snapshot()
    assert snap.active == 0
    assert snap.maximum == 3

    assert gate.acquire() is True
    snap = gate.snapshot()
    assert snap.active == 1

    assert gate.acquire() is True
    snap = gate.snapshot()
    assert snap.active == 2

    gate.release()
    snap = gate.snapshot()
    assert snap.active == 1

    gate.release()
    snap = gate.snapshot()
    assert snap.active == 0


def test_gate_respects_maximum_capacity():
    gate = ObservableProcessGate(max_concurrent=2)
    assert gate.acquire() is True
    assert gate.acquire() is True

    # Third acquire non-blocking should fail
    assert gate.acquire(blocking=False) is False
    assert gate.snapshot().active == 2

    gate.release()
    assert gate.snapshot().active == 1
    assert gate.acquire(blocking=False) is True
    assert gate.snapshot().active == 2


def test_composite_gate_with_observable_gate():
    service_gate = ObservableProcessGate(max_concurrent=4)
    comp_gate = process_gate(per_stream_limit=2, service_gate=service_gate)

    assert comp_gate.acquire() is True
    assert service_gate.snapshot().active == 1

    assert comp_gate.acquire() is True
    assert service_gate.snapshot().active == 2

    comp_gate.release()
    assert service_gate.snapshot().active == 1

    comp_gate.release()
    assert service_gate.snapshot().active == 0


def test_concurrent_acquire_release_consistency():
    gate = ObservableProcessGate(max_concurrent=10)
    iterations = 200

    def worker():
        for _ in range(iterations):
            if gate.acquire(timeout=1.0):
                gate.release()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker) for _ in range(8)]
        for f in futures:
            f.result()

    snap = gate.snapshot()
    assert snap.active == 0
    assert snap.maximum == 10


def test_concurrent_snapshots_never_leave_capacity_bounds():
    gate = ObservableProcessGate(max_concurrent=3)
    stop = threading.Event()
    observations = []

    def worker():
        for _ in range(500):
            assert gate.acquire(timeout=1.0)
            gate.release()

    def observer():
        while not stop.is_set():
            observations.append(gate.snapshot().active)

    observer_thread = threading.Thread(target=observer)
    observer_thread.start()
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker) for _ in range(8)]
            for future in futures:
                future.result()
    finally:
        stop.set()
        observer_thread.join()

    assert observations
    assert all(0 <= active <= gate.maximum for active in observations)


def test_release_without_acquire_is_rejected():
    gate = ObservableProcessGate(max_concurrent=1)

    with pytest.raises(ValueError, match="released too many times"):
        gate.release()

from threading import Event, Thread
import time

from core.media_process_budget import process_gate


def test_service_gate_caps_media_processes_across_stream_gates():
    service_gate = process_gate(per_stream_limit=1)
    stream_gates = [
        process_gate(per_stream_limit=2, service_gate=service_gate)
        for _ in range(3)
    ]
    release = Event()
    started = Event()
    active = 0
    maximum = 0

    def run(gate):
        nonlocal active, maximum
        gate.acquire()
        try:
            active += 1
            maximum = max(maximum, active)
            started.set()
            release.wait(1.0)
        finally:
            active -= 1
            gate.release()

    threads = [Thread(target=run, args=(gate,)) for gate in stream_gates]
    for thread in threads:
        thread.start()
    assert started.wait(0.5)
    time.sleep(0.05)
    assert maximum == 1
    release.set()
    for thread in threads:
        thread.join(1.0)
        assert not thread.is_alive()

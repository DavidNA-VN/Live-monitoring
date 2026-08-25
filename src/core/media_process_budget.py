from __future__ import annotations

from threading import BoundedSemaphore
from typing import Protocol


class ProcessGate(Protocol):
    def acquire(self) -> bool:
        ...

    def release(self) -> None:
        ...


class CompositeProcessGate:
    """Acquires per-stream and service-wide process budgets together."""

    def __init__(self, *gates: ProcessGate) -> None:
        if not gates:
            raise ValueError("At least one process gate is required")
        self.gates = gates

    def acquire(self) -> bool:
        acquired: list[ProcessGate] = []
        try:
            for gate in self.gates:
                gate.acquire()
                acquired.append(gate)
        except BaseException:
            for gate in reversed(acquired):
                gate.release()
            raise
        return True

    def release(self) -> None:
        for gate in reversed(self.gates):
            gate.release()


def process_gate(
    *,
    per_stream_limit: int,
    service_gate: ProcessGate | None = None,
) -> ProcessGate:
    local = BoundedSemaphore(per_stream_limit)
    if service_gate is None:
        return local
    return CompositeProcessGate(local, service_gate)

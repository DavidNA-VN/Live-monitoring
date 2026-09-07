from __future__ import annotations

from dataclasses import dataclass
from threading import Condition
from time import monotonic
from typing import Protocol


class ProcessGate(Protocol):
    def acquire(self) -> bool:
        ...

    def release(self) -> None:
        ...


@dataclass(frozen=True)
class MediaProcessCapacitySnapshot:
    active: int
    maximum: int


class ObservableProcessGate:
    """Service-wide process budget with an atomically observable active count."""

    def __init__(self, max_concurrent: int) -> None:
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be > 0")
        self.maximum = max_concurrent
        self._active = 0
        self._condition = Condition()

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        if not blocking and timeout is not None:
            raise ValueError("timeout is not supported for non-blocking acquire")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be >= 0")

        with self._condition:
            if not blocking:
                if self._active >= self.maximum:
                    return False
            elif timeout is None:
                while self._active >= self.maximum:
                    self._condition.wait()
            else:
                deadline = monotonic() + timeout
                while self._active >= self.maximum:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return False
                    self._condition.wait(remaining)

            self._active += 1
            return True

    def release(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise ValueError("Process gate released too many times")
            self._active -= 1
            self._condition.notify()

    def snapshot(self) -> MediaProcessCapacitySnapshot:
        with self._condition:
            return MediaProcessCapacitySnapshot(
                active=self._active,
                maximum=self.maximum,
            )


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
                if not gate.acquire():
                    for acquired_gate in reversed(acquired):
                        acquired_gate.release()
                    return False
                acquired.append(gate)
        except BaseException:
            for gate in reversed(acquired):
                gate.release()
            raise
        return True

    def release(self) -> None:
        for gate in reversed(self.gates):
            gate.release()

    def snapshot(self) -> MediaProcessCapacitySnapshot:
        local = self.gates[0]
        snapshot = getattr(local, "snapshot", None)
        if not callable(snapshot):
            raise RuntimeError("Local process gate is not observable")
        return snapshot()


def process_gate(
    *,
    per_stream_limit: int,
    service_gate: ProcessGate | None = None,
) -> ProcessGate:
    local = ObservableProcessGate(per_stream_limit)
    if service_gate is None:
        return local
    return CompositeProcessGate(local, service_gate)

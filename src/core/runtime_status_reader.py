from __future__ import annotations

from typing import Protocol, runtime_checkable

from models.runtime_status import RuntimeStatus


class RuntimeStatusReadError(RuntimeError):
    pass


@runtime_checkable
class RuntimeStatusReader(Protocol):
    def get(self, stream_id: str) -> RuntimeStatus | None:
        ...

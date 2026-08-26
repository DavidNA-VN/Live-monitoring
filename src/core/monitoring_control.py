from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from models.stream_config import StreamConfig


class MonitoringAction(str, Enum):
    START = "START"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"
    UPDATE_CONFIG = "UPDATE_CONFIG"


@dataclass(frozen=True)
class MonitoringControlResult:
    stream_id: str
    action: MonitoringAction
    changed: bool


@runtime_checkable
class MonitoringControl(Protocol):
    def start(self, config: StreamConfig) -> MonitoringControlResult:
        ...

    def pause(self, stream_id: str) -> MonitoringControlResult:
        ...

    def resume(self, stream_id: str) -> MonitoringControlResult:
        ...

    def stop(self, stream_id: str) -> MonitoringControlResult:
        ...

    def update_config(
        self,
        stream_id: str,
        config: StreamConfig,
    ) -> MonitoringControlResult:
        ...


class MonitoringControlError(RuntimeError):
    pass


class StreamAlreadyExistsError(MonitoringControlError):
    pass


class StreamNotFoundError(MonitoringControlError):
    pass


class StreamCapacityError(MonitoringControlError):
    pass


class StreamIdentityMismatchError(MonitoringControlError):
    pass


class StreamOperationError(MonitoringControlError):
    pass

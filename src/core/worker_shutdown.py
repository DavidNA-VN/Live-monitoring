from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShutdownReport:
    """Detailed summary of the worker shutdown process."""

    commands_drained: bool
    heartbeat_stopped: bool
    projection_stopped: bool
    streams_drained: bool
    redis_closed: bool
    timed_out: bool
    errors: tuple[str, ...] = ()

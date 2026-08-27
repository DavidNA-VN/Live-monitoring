from __future__ import annotations

from typing import Protocol

from models.worker_heartbeat import WorkerHeartbeat


class WorkerHeartbeatPublishError(RuntimeError):
    """Raised when worker heartbeat publication fails."""


class WorkerHeartbeatPublisher(Protocol):
    """Public boundary port protocol for worker heartbeat and discovery publishing."""

    def publish(
        self,
        heartbeat: WorkerHeartbeat,
        *,
        ttl_seconds: int = 15,
        discovery_window_seconds: int = 30,
    ) -> None:
        """Publish worker heartbeat snapshot and update the active worker registry."""
        ...

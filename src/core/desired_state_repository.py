from __future__ import annotations

from typing import Protocol

from models.desired_stream_state import DesiredStreamState


class DesiredStateRepositoryError(RuntimeError):
    """Base exception for desired state repository errors."""


class DesiredStateUnavailableError(DesiredStateRepositoryError):
    """Raised when Redis or backend storage is unreachable or fails."""


class DesiredStateCorruptedError(DesiredStateRepositoryError):
    """Raised when a desired state record payload in storage is corrupt."""


class DesiredStatePersistenceError(DesiredStateRepositoryError):
    """Raised when persisting desired state fails after lifecycle operation."""


class DesiredStateRepository(Protocol):
    """Repository port for desired stream state persistence and quarantine."""

    def get(self, stream_id: str) -> DesiredStreamState | None:
        """Fetch desired state for a stream, returning None if not found."""
        ...

    def list_all(self) -> list[DesiredStreamState]:
        """Fetch all desired stream state records currently registered."""
        ...

    def save(self, record: DesiredStreamState) -> None:
        """Persist or update a desired stream state record."""
        ...

    def delete(self, stream_id: str) -> bool:
        """Remove a desired stream state record."""
        ...

    def quarantine(self, stream_id: str, raw_payload: str, error: str) -> None:
        """Atomically archive and remove an invalid active desired-state record."""
        ...

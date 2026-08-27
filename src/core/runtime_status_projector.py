from __future__ import annotations

from datetime import datetime
from typing import Protocol

from models.runtime_status import RuntimeStatus


class RuntimeStatusProjector(Protocol):
    """Public projection boundary protocol for external stream runtime statuses."""

    def project(self, status: RuntimeStatus) -> bool:
        """Project current status to public transport.

        Returns:
            True if a status update event was published to the update stream.
            False if semantic state is unchanged and only the current status snapshot was refreshed.
        """
        ...

    def remove(
        self,
        *,
        stream_id: str,
        worker_id: str,
        observed_at: datetime,
    ) -> bool:
        """Remove a stream's status from public transport when stopped or removed.

        Returns:
            True if a REMOVED event was published and current snapshot was deleted.
            False if the stream was already absent or belongs to a different worker.
        """
        ...

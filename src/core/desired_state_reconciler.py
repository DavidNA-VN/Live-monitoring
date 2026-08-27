from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class RecoveryReport:
    total_records: int = 0
    running_started: int = 0
    paused_registered: int = 0
    stopped_skipped: int = 0
    errors: dict[str, str] = field(default_factory=dict)


class DesiredStateReconciler(Protocol):
    """Port protocol for reconciling active supervisor sessions against desired state registry."""

    def reconcile(self) -> RecoveryReport:
        """Reconcile supervisor sessions with desired stream states."""
        ...

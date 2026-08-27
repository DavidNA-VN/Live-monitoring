from __future__ import annotations

import logging

from core.desired_state_reconciler import (
    DesiredStateReconciler,
    RecoveryReport,
)
from core.desired_state_repository import DesiredStateRepository
from core.monitoring_control import MonitoringControl
from core.stream_supervisor import (
    StreamSupervisor,
    StreamSupervisorAlreadyRegisteredError,
)
from models.desired_stream_state import DesiredLifecycleState

logger = logging.getLogger(__name__)


class SupervisorDesiredStateReconciler(DesiredStateReconciler):
    """Reconciles in-memory supervisor stream sessions against persistent desired state registry."""

    def __init__(
        self,
        *,
        supervisor: StreamSupervisor,
        control: MonitoringControl,
        repository: DesiredStateRepository,
    ) -> None:
        self.supervisor = supervisor
        self.control = control
        self.repository = repository

    def reconcile(self) -> RecoveryReport:
        records = self.repository.list_all()
        running_started = 0
        paused_registered = 0
        stopped_skipped = 0
        errors: dict[str, str] = {}

        for record in records:
            stream_id = record.stream_id
            if record.desired_state == DesiredLifecycleState.RUNNING:
                if record.config is None:
                    errors[stream_id] = (
                        "Desired state RUNNING missing stream configuration"
                    )
                    continue
                try:
                    self.control.start(record.config)
                    running_started += 1
                except Exception as exc:
                    logger.exception(
                        "Recovery failed to start desired RUNNING stream '%s'",
                        stream_id,
                    )
                    errors[stream_id] = str(exc)

            elif record.desired_state == DesiredLifecycleState.PAUSED:
                if record.config is None:
                    errors[stream_id] = (
                        "Desired state PAUSED missing stream configuration"
                    )
                    continue
                try:
                    existing = self.supervisor.configuration(stream_id)
                    if existing is None:
                        self.supervisor.add(record.config, start=False)
                    elif existing != record.config:
                        raise ValueError(
                            "Registered stream configuration does not match "
                            "desired PAUSED configuration"
                        )
                    self.supervisor.pause(stream_id)
                    paused_registered += 1
                except StreamSupervisorAlreadyRegisteredError:
                    # Idempotent re-run
                    self.supervisor.pause(stream_id)
                    paused_registered += 1
                except Exception as exc:
                    logger.exception(
                        "Recovery failed to register desired PAUSED stream '%s'",
                        stream_id,
                    )
                    errors[stream_id] = str(exc)

            elif record.desired_state == DesiredLifecycleState.STOPPED:
                # Tombstone record: do not resurrect or create session
                stopped_skipped += 1

        return RecoveryReport(
            total_records=len(records),
            running_started=running_started,
            paused_registered=paused_registered,
            stopped_skipped=stopped_skipped,
            errors=errors,
        )

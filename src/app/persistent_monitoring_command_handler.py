from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Protocol

from app.stream_config_mapper import stream_config_from_public
from core.desired_state_repository import (
    DesiredStatePersistenceError,
    DesiredStateRepository,
)
from core.monitoring_control import MonitoringAction
from core.stream_supervisor import StreamSupervisor
from core.stream_session import StreamSessionStatus
from models.desired_stream_state import (
    DesiredLifecycleState,
    DesiredStreamState,
)
from models.monitoring_command import (
    MonitoringCommand,
    MonitoringCommandResult,
    MonitoringCommandResultStatus,
)

logger = logging.getLogger(__name__)


class MonitoringCommandExecutor(Protocol):
    def handle(self, command: MonitoringCommand) -> MonitoringCommandResult:
        ...


class PersistentMonitoringCommandHandler:
    """Decorator executing lifecycle command and persisting the desired state on success."""

    def __init__(
        self,
        *,
        inner_handler: MonitoringCommandExecutor,
        repository: DesiredStateRepository,
        supervisor: StreamSupervisor,
    ) -> None:
        self.inner_handler = inner_handler
        self.repository = repository
        self.supervisor = supervisor

    def handle(self, command: MonitoringCommand) -> MonitoringCommandResult:
        result = self.inner_handler.handle(command)

        # Do not persist desired state if lifecycle operation failed or was rejected
        if result.status in (
            MonitoringCommandResultStatus.REJECTED,
            MonitoringCommandResultStatus.FAILED,
        ):
            return result

        try:
            self._persist_desired_transition(command)
        except DesiredStatePersistenceError:
            raise
        except Exception as exc:
            raise DesiredStatePersistenceError(
                f"Failed to persist desired state for stream '{command.stream_id}': {exc}"
            ) from exc

        return result

    def _persist_desired_transition(self, command: MonitoringCommand) -> None:
        now = datetime.now(timezone.utc)
        stream_id = command.stream_id

        if command.action == MonitoringAction.START:
            config = stream_config_from_public(command.config)
            record = DesiredStreamState(
                stream_id=stream_id,
                desired_state=DesiredLifecycleState.RUNNING,
                updated_at=now,
                config=config,
                last_command_id=command.command_id,
            )
        elif command.action == MonitoringAction.PAUSE:
            config = self._resolve_config(stream_id)
            record = DesiredStreamState(
                stream_id=stream_id,
                desired_state=DesiredLifecycleState.PAUSED,
                updated_at=now,
                config=config,
                last_command_id=command.command_id,
            )
        elif command.action == MonitoringAction.RESUME:
            config = self._resolve_config(stream_id)
            record = DesiredStreamState(
                stream_id=stream_id,
                desired_state=DesiredLifecycleState.RUNNING,
                updated_at=now,
                config=config,
                last_command_id=command.command_id,
            )
        elif command.action == MonitoringAction.UPDATE_CONFIG:
            config = stream_config_from_public(command.config)
            existing = self.repository.get(stream_id)
            desired_state = self._resolve_active_state(stream_id, existing)
            record = DesiredStreamState(
                stream_id=stream_id,
                desired_state=desired_state,
                updated_at=now,
                config=config,
                last_command_id=command.command_id,
            )
        elif command.action == MonitoringAction.STOP:
            record = DesiredStreamState(
                stream_id=stream_id,
                desired_state=DesiredLifecycleState.STOPPED,
                updated_at=now,
                config=None,
                last_command_id=command.command_id,
            )
        else:
            return

        self.repository.save(record)

    def _resolve_config(self, stream_id: str):
        existing = self.repository.get(stream_id)
        if existing is not None and existing.config is not None:
            return existing.config
        current = self.supervisor.configuration(stream_id)
        if current is not None:
            return current
        raise ValueError(f"Cannot resolve stream configuration for '{stream_id}'")

    def _resolve_active_state(
        self,
        stream_id: str,
        existing: DesiredStreamState | None,
    ) -> DesiredLifecycleState:
        if existing is not None and existing.desired_state in (
            DesiredLifecycleState.RUNNING,
            DesiredLifecycleState.PAUSED,
        ):
            return existing.desired_state
        snapshot = self.supervisor.snapshot(stream_id)
        if snapshot is not None and snapshot.status == StreamSessionStatus.PAUSED:
            return DesiredLifecycleState.PAUSED
        return DesiredLifecycleState.RUNNING

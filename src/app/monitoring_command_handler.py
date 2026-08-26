from __future__ import annotations

from datetime import datetime, timezone

from app.stream_config_mapper import stream_config_from_public
from core.monitoring_control import MonitoringAction, MonitoringControl
from models.monitoring_command import (
    MonitoringCommand,
    MonitoringCommandResult,
    MonitoringCommandResultStatus,
)


class MonitoringCommandHandler:
    def __init__(self, control: MonitoringControl) -> None:
        self.control = control

    def handle(self, command: MonitoringCommand) -> MonitoringCommandResult:
        if command.action in (MonitoringAction.START, MonitoringAction.UPDATE_CONFIG):
            config = stream_config_from_public(command.config)
            if config.identity.external_stream_id != command.stream_id:
                raise ValueError(
                    "command stream_id does not match config stream_id"
                )
            result = (
                self.control.start(config)
                if command.action == MonitoringAction.START
                else self.control.update_config(command.stream_id, config)
            )
        elif command.action == MonitoringAction.PAUSE:
            result = self.control.pause(command.stream_id)
        elif command.action == MonitoringAction.RESUME:
            result = self.control.resume(command.stream_id)
        elif command.action == MonitoringAction.STOP:
            result = self.control.stop(command.stream_id)
        else:
            raise ValueError(f"Unsupported monitoring action: {command.action}")
        return MonitoringCommandResult(
            command_id=command.command_id,
            action=command.action,
            stream_id=command.stream_id,
            status=(
                MonitoringCommandResultStatus.APPLIED
                if result.changed
                else MonitoringCommandResultStatus.NOOP
            ),
            changed=result.changed,
            processed_at=datetime.now(timezone.utc),
        )

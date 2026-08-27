from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from models.stream_config import StreamConfig


DESIRED_STATE_SCHEMA_VERSION = "1.0"


class DesiredLifecycleState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class DesiredStreamState:
    stream_id: str
    desired_state: DesiredLifecycleState
    updated_at: datetime
    config: StreamConfig | None = None
    last_command_id: str | None = None
    schema_version: str = DESIRED_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.stream_id, str) or not self.stream_id.strip():
            raise ValueError("stream_id must be a non-empty string")
        if len(self.stream_id) > 128:
            raise ValueError("stream_id must not exceed 128 characters")
        if self.schema_version != DESIRED_STATE_SCHEMA_VERSION:
            raise ValueError("unsupported desired state schema_version")
        if not isinstance(self.desired_state, DesiredLifecycleState):
            raise ValueError(f"Invalid desired_state: {self.desired_state}")
        if not isinstance(self.updated_at, datetime):
            raise ValueError("updated_at must be a datetime")

        # Normalize datetime to UTC
        if self.updated_at.tzinfo is None:
            object.__setattr__(
                self, "updated_at", self.updated_at.replace(tzinfo=timezone.utc)
            )
        else:
            object.__setattr__(
                self, "updated_at", self.updated_at.astimezone(timezone.utc)
            )

        if self.desired_state in (
            DesiredLifecycleState.RUNNING,
            DesiredLifecycleState.PAUSED,
        ):
            if self.config is None:
                raise ValueError(
                    f"config must be provided when desired_state is {self.desired_state.value}"
                )
            if self.config.identity.external_stream_id != self.stream_id:
                raise ValueError(
                    f"config stream_id '{self.config.identity.external_stream_id}' does not match '{self.stream_id}'"
                )
        elif self.desired_state == DesiredLifecycleState.STOPPED:
            if self.config is not None:
                raise ValueError("config must be None when desired_state is STOPPED")

        if self.last_command_id is not None:
            if (
                not isinstance(self.last_command_id, str)
                or not self.last_command_id.strip()
            ):
                raise ValueError("last_command_id must be a non-empty string or None")

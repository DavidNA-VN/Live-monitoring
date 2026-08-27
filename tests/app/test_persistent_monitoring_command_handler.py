from datetime import datetime, timezone
import pytest

from app.persistent_monitoring_command_handler import (
    PersistentMonitoringCommandHandler,
)
from core.desired_state_repository import (
    DesiredStatePersistenceError,
    DesiredStateRepository,
)
from core.monitoring_control import MonitoringAction
from models.desired_stream_state import (
    DesiredLifecycleState,
    DesiredStreamState,
)
from models.monitoring_command import (
    MonitoringCommand,
    MonitoringCommandResult,
    MonitoringCommandResultStatus,
)
from models.stream_config import StreamConfig


class InMemoryDesiredStateRepo(DesiredStateRepository):
    def __init__(self):
        self.records: dict[str, DesiredStreamState] = {}
        self.fail_save: bool = False

    def get(self, stream_id: str) -> DesiredStreamState | None:
        return self.records.get(stream_id)

    def list_all(self) -> list[DesiredStreamState]:
        return list(self.records.values())

    def save(self, record: DesiredStreamState) -> None:
        if self.fail_save:
            raise DesiredStatePersistenceError("Simulated persistence error")
        self.records[record.stream_id] = record

    def delete(self, stream_id: str) -> bool:
        return bool(self.records.pop(stream_id, None))

    def quarantine(self, stream_id: str, raw_payload: str, error: str) -> None:
        pass


class DummySupervisor:
    def __init__(self):
        self.configs: dict[str, StreamConfig] = {}

    def configuration(self, stream_id: str) -> StreamConfig | None:
        return self.configs.get(stream_id)


class DummyInnerHandler:
    def __init__(self):
        self.result_status = MonitoringCommandResultStatus.APPLIED
        self.last_command = None

    def handle(self, command: MonitoringCommand) -> MonitoringCommandResult:
        self.last_command = command
        return MonitoringCommandResult(
            command_id=command.command_id,
            action=command.action,
            stream_id=command.stream_id,
            status=self.result_status,
            changed=self.result_status == MonitoringCommandResultStatus.APPLIED,
            processed_at=datetime.now(timezone.utc),
        )


def make_command(action: MonitoringAction, stream_id: str = "channel-01", config: dict | None = None):
    if config is None and action in (MonitoringAction.START, MonitoringAction.UPDATE_CONFIG):
        config = {
            "schema_version": "1.0",
            "stream_id": stream_id,
            "master_url": "https://example.test/live.m3u8",
            "checks": {
                "black_screen": {"enabled": True},
                "audio_loss": {
                    "enabled": True,
                    "threshold_dbfs": -60.0,
                    "duration_seconds": 30.0,
                    "track_index": 0,
                },
            },
        }
    return MonitoringCommand(
        command_id="cmd-1",
        action=action,
        stream_id=stream_id,
        requested_at=datetime.now(timezone.utc),
        config=config,
    )


def test_start_command_persists_running_desired_state():
    repo = InMemoryDesiredStateRepo()
    supervisor = DummySupervisor()
    inner = DummyInnerHandler()
    handler = PersistentMonitoringCommandHandler(
        inner_handler=inner,
        repository=repo,
        supervisor=supervisor,
    )

    cmd = make_command(MonitoringAction.START, "channel-01")
    result = handler.handle(cmd)

    assert result.status == MonitoringCommandResultStatus.APPLIED
    record = repo.get("channel-01")
    assert record is not None
    assert record.desired_state == DesiredLifecycleState.RUNNING
    assert record.config is not None
    assert record.config.master_url == "https://example.test/live.m3u8"
    assert record.last_command_id == "cmd-1"


def test_pause_and_resume_commands_persist_transitions():
    repo = InMemoryDesiredStateRepo()
    supervisor = DummySupervisor()
    inner = DummyInnerHandler()
    handler = PersistentMonitoringCommandHandler(
        inner_handler=inner,
        repository=repo,
        supervisor=supervisor,
    )

    # 1. Start
    handler.handle(make_command(MonitoringAction.START, "channel-01"))
    assert repo.get("channel-01").desired_state == DesiredLifecycleState.RUNNING

    # 2. Pause
    handler.handle(make_command(MonitoringAction.PAUSE, "channel-01"))
    assert repo.get("channel-01").desired_state == DesiredLifecycleState.PAUSED
    assert repo.get("channel-01").config is not None  # Preserves config!

    # 3. Resume
    handler.handle(make_command(MonitoringAction.RESUME, "channel-01"))
    assert repo.get("channel-01").desired_state == DesiredLifecycleState.RUNNING


def test_stop_command_persists_stopped_tombstone():
    repo = InMemoryDesiredStateRepo()
    supervisor = DummySupervisor()
    inner = DummyInnerHandler()
    handler = PersistentMonitoringCommandHandler(
        inner_handler=inner,
        repository=repo,
        supervisor=supervisor,
    )

    handler.handle(make_command(MonitoringAction.START, "channel-01"))
    handler.handle(make_command(MonitoringAction.STOP, "channel-01"))

    record = repo.get("channel-01")
    assert record is not None
    assert record.desired_state == DesiredLifecycleState.STOPPED
    assert record.config is None


def test_failed_lifecycle_does_not_persist():
    repo = InMemoryDesiredStateRepo()
    supervisor = DummySupervisor()
    inner = DummyInnerHandler()
    inner.result_status = MonitoringCommandResultStatus.REJECTED
    handler = PersistentMonitoringCommandHandler(
        inner_handler=inner,
        repository=repo,
        supervisor=supervisor,
    )

    handler.handle(make_command(MonitoringAction.START, "channel-01"))
    assert repo.get("channel-01") is None


def test_persistence_failure_bubbles_desired_state_persistence_error():
    repo = InMemoryDesiredStateRepo()
    repo.fail_save = True
    supervisor = DummySupervisor()
    inner = DummyInnerHandler()
    handler = PersistentMonitoringCommandHandler(
        inner_handler=inner,
        repository=repo,
        supervisor=supervisor,
    )

    with pytest.raises(DesiredStatePersistenceError):
        handler.handle(make_command(MonitoringAction.START, "channel-01"))

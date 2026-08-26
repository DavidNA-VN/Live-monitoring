from datetime import datetime, timezone

import pytest

from app.monitoring_command_handler import MonitoringCommandHandler
from app.stream_config_mapper import (
    StreamConfigMappingError,
    stream_config_from_public,
)
from core.monitoring_control import (
    MonitoringAction,
    MonitoringControlResult,
)
from models.monitoring_command import (
    MonitoringCommand,
    MonitoringCommandError,
    MonitoringCommandResultStatus,
)


def public_config(stream_id="channel-01"):
    return {
        "schema_version": "1.0",
        "stream_id": stream_id,
        "master_url": "https://example.test/master.m3u8",
        "checks": {
            "black_screen": {"enabled": True},
            "audio_loss": {
                "enabled": True,
                "threshold_dbfs": -60,
                "duration_seconds": 30,
                "track_index": 0,
            },
        },
    }


def command(action=MonitoringAction.START, config=None):
    return MonitoringCommand(
        command_id="cmd-1",
        action=action,
        stream_id="channel-01",
        requested_at=datetime.now(timezone.utc),
        config=config,
    )


class RecordingControl:
    def __init__(self, changed=True):
        self.changed = changed
        self.calls = []

    def _result(self, action, stream_id):
        self.calls.append((action, stream_id))
        return MonitoringControlResult(stream_id, action, self.changed)

    def start(self, config):
        return self._result(MonitoringAction.START, config.stream_id)

    def pause(self, stream_id):
        return self._result(MonitoringAction.PAUSE, stream_id)

    def resume(self, stream_id):
        return self._result(MonitoringAction.RESUME, stream_id)

    def stop(self, stream_id):
        return self._result(MonitoringAction.STOP, stream_id)

    def update_config(self, stream_id, config):
        return self._result(MonitoringAction.UPDATE_CONFIG, stream_id)


def test_public_config_maps_only_supported_fields():
    config = stream_config_from_public(public_config())

    assert config.stream_id == "channel-01"
    assert config.black_screen_enabled is True
    assert config.audio_loss_enabled is True
    assert config.silence_threshold_dbfs == -60
    assert config.audio_loss_duration == 30


def test_public_config_rejects_non_finite_threshold():
    payload = public_config()
    payload["checks"]["audio_loss"]["threshold_dbfs"] = float("nan")

    with pytest.raises(StreamConfigMappingError):
        stream_config_from_public(payload)


def test_command_parser_requires_timezone_and_start_config():
    payload = {
        "schema_version": "1.0",
        "command_id": "cmd-1",
        "command_type": "START",
        "stream_id": "channel-01",
        "requested_at": "2026-08-26T10:00:00",
    }

    with pytest.raises(MonitoringCommandError):
        MonitoringCommand.from_dict(payload)


def test_command_parser_rejects_invalid_utf8():
    with pytest.raises(MonitoringCommandError):
        MonitoringCommand.from_json(b"\xff")


@pytest.mark.parametrize(
    "action",
    [
        MonitoringAction.PAUSE,
        MonitoringAction.RESUME,
        MonitoringAction.STOP,
    ],
)
def test_handler_routes_simple_actions(action):
    control = RecordingControl()
    result = MonitoringCommandHandler(control).handle(command(action))

    assert result.status is MonitoringCommandResultStatus.APPLIED
    assert control.calls == [(action, "channel-01")]


def test_handler_maps_start_and_noop_result():
    control = RecordingControl(changed=False)
    result = MonitoringCommandHandler(control).handle(
        command(MonitoringAction.START, public_config())
    )

    assert result.status is MonitoringCommandResultStatus.NOOP
    assert result.changed is False


def test_handler_rejects_command_config_identity_mismatch():
    with pytest.raises(ValueError, match="does not match"):
        MonitoringCommandHandler(RecordingControl()).handle(
            command(MonitoringAction.START, public_config("channel-02"))
        )

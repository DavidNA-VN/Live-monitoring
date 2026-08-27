from datetime import datetime, timezone
import pytest

from app.desired_state_codec import (
    desired_state_from_dict,
    desired_state_to_dict,
)
from models.desired_stream_state import (
    DesiredLifecycleState,
    DesiredStreamState,
)
from models.stream_config import StreamConfig


def sample_config(stream_id="channel-01"):
    return StreamConfig(
        stream_id=stream_id,
        master_url="https://example.test/live.m3u8",
        black_screen_enabled=True,
        audio_loss_enabled=True,
        silence_threshold_dbfs=-55.0,
        audio_loss_duration=25.0,
        audio_track_index=1,
    )


def test_desired_stream_state_serialization_and_deserialization():
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    cfg = sample_config()
    record = DesiredStreamState(
        stream_id="channel-01",
        desired_state=DesiredLifecycleState.RUNNING,
        updated_at=now,
        config=cfg,
        last_command_id="cmd-123",
    )

    data = desired_state_to_dict(record)
    assert data["schema_version"] == "1.0"
    assert data["stream_id"] == "channel-01"
    assert data["desired_state"] == "RUNNING"
    assert data["updated_at"] == "2026-08-27T10:00:00+00:00"
    assert data["last_command_id"] == "cmd-123"
    assert data["config"]["master_url"] == "https://example.test/live.m3u8"
    assert data["config"]["checks"]["black_screen"]["enabled"] is True
    assert data["config"]["checks"]["audio_loss"]["track_index"] == 1

    # Ensure no internal leakage
    assert "storage_id" not in data
    assert "storage_id" not in data["config"]

    restored = desired_state_from_dict(data)
    assert restored.stream_id == "channel-01"
    assert restored.desired_state == DesiredLifecycleState.RUNNING
    assert restored.updated_at == now
    assert restored.last_command_id == "cmd-123"
    assert restored.config is not None
    assert restored.config.identity.external_stream_id == "channel-01"
    assert restored.config.silence_threshold_dbfs == -55.0


def test_desired_stream_state_stopped_tombstone():
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    record = DesiredStreamState(
        stream_id="channel-02",
        desired_state=DesiredLifecycleState.STOPPED,
        updated_at=now,
        config=None,
        last_command_id="cmd-stop-01",
    )

    data = desired_state_to_dict(record)
    assert data["desired_state"] == "STOPPED"
    assert data["config"] is None

    restored = desired_state_from_dict(data)
    assert restored.desired_state == DesiredLifecycleState.STOPPED
    assert restored.config is None


def test_desired_stream_state_validations():
    now = datetime.now(timezone.utc)
    cfg = sample_config("channel-01")

    # Missing config when RUNNING
    with pytest.raises(ValueError, match="config must be provided"):
        DesiredStreamState(
            stream_id="channel-01",
            desired_state=DesiredLifecycleState.RUNNING,
            updated_at=now,
            config=None,
        )

    # Missing config when PAUSED
    with pytest.raises(ValueError, match="config must be provided"):
        DesiredStreamState(
            stream_id="channel-01",
            desired_state=DesiredLifecycleState.PAUSED,
            updated_at=now,
            config=None,
        )

    # Config present when STOPPED
    with pytest.raises(ValueError, match="config must be None"):
        DesiredStreamState(
            stream_id="channel-01",
            desired_state=DesiredLifecycleState.STOPPED,
            updated_at=now,
            config=cfg,
        )

    # Mismatched stream_id
    with pytest.raises(ValueError, match="does not match"):
        DesiredStreamState(
            stream_id="channel-other",
            desired_state=DesiredLifecycleState.RUNNING,
            updated_at=now,
            config=cfg,
        )

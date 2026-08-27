import json
from pathlib import Path
import pytest

from app.stream_config_mapper import (
    stream_config_from_public,
    stream_config_to_public,
)
from models.stream_config import StreamConfig

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"


def test_stream_config_to_public_serialization():
    config = StreamConfig(
        stream_id="channel-01",
        master_url="https://example.test/stream.m3u8",
        black_screen_enabled=True,
        audio_loss_enabled=True,
        silence_threshold_dbfs=-60.0,
        audio_loss_duration=30.0,
        audio_track_index=2,
    )

    public_dict = stream_config_to_public(config)
    assert public_dict["schema_version"] == "1.0"
    assert public_dict["stream_id"] == "channel-01"
    assert public_dict["master_url"] == "https://example.test/stream.m3u8"
    assert public_dict["checks"]["black_screen"]["enabled"] is True
    assert public_dict["checks"]["audio_loss"]["enabled"] is True
    assert public_dict["checks"]["audio_loss"]["threshold_dbfs"] == -60.0
    assert public_dict["checks"]["audio_loss"]["duration_seconds"] == 30.0
    assert public_dict["checks"]["audio_loss"]["track_index"] == 2
    assert "storage_id" not in public_dict

    # Verify round-trip mapping
    reconstructed = stream_config_from_public(public_dict)
    assert reconstructed.stream_id == config.stream_id
    assert reconstructed.master_url == config.master_url
    assert reconstructed.black_screen_enabled == config.black_screen_enabled
    assert reconstructed.audio_loss_enabled == config.audio_loss_enabled
    assert reconstructed.silence_threshold_dbfs == config.silence_threshold_dbfs
    assert reconstructed.audio_loss_duration == config.audio_loss_duration
    assert reconstructed.audio_track_index == config.audio_track_index

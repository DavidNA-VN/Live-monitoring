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
        video_freeze_enabled=True,
        freeze_noise_db=-55.0,
        freeze_detector_minimum_duration=0.3,
        freeze_warning_duration=4.0,
        freeze_alert_duration=6.0,
    )

    public_dict = stream_config_to_public(config)
    assert public_dict["schema_version"] == "1.0"
    assert public_dict["stream_id"] == "channel-01"
    assert public_dict["master_url"] == "https://example.test/stream.m3u8"
    assert public_dict["admission"] == {
        "startup_mode": "bounded_history",
        "startup_lookback_segments": 4,
        "soft_lag_target_durations": 2.0,
        "recovery_lag_target_durations": 1.5,
        "hard_lag_target_durations": 6.0,
        "live_edge_retention_segments": 2,
        "transition_cycles": 3,
    }
    assert public_dict["variant_selection"] == {
        "mode": "all",
        "representative_count": 3,
        "explicit_variant_ids": [],
    }
    assert public_dict["checks"]["black_screen"]["enabled"] is True
    assert public_dict["checks"]["audio_loss"]["enabled"] is True
    assert public_dict["checks"]["audio_loss"]["threshold_dbfs"] == -60.0
    assert public_dict["checks"]["audio_loss"]["duration_seconds"] == 30.0
    assert public_dict["checks"]["audio_loss"]["track_index"] == 2
    assert public_dict["checks"]["video_freeze"] == {
        "enabled": True,
        "noise_db": -55.0,
        "detector_minimum_duration": 0.3,
        "warning_duration_seconds": 4.0,
        "alert_duration_seconds": 6.0,
    }
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
    assert reconstructed.video_freeze_enabled is True
    assert reconstructed.freeze_noise_db == -55.0
    assert reconstructed.freeze_detector_minimum_duration == 0.3
    assert reconstructed.freeze_warning_duration == 4.0
    assert reconstructed.freeze_alert_duration == 6.0
    assert reconstructed.admission_policy.startup_lookback_segments == 4
    assert reconstructed.admission_policy.soft_lag_target_durations == 2.0
    assert reconstructed.admission_policy.recovery_lag_target_durations == 1.5
    assert reconstructed.admission_policy.hard_lag_target_durations == 6.0
    assert reconstructed.admission_policy.live_edge_retention_segments == 2
    assert reconstructed.variant_selection.mode.value == "all"
    assert reconstructed.admission_policy.transition_cycles == 3


def test_legacy_public_config_defaults_freeze_to_disabled():
    public = {
        "schema_version": "1.0",
        "stream_id": "channel-01",
        "master_url": "https://example.test/master.m3u8",
        "checks": {
            "black_screen": {"enabled": True},
            "audio_loss": {
                "enabled": True,
                "threshold_dbfs": -60.0,
                "duration_seconds": 30.0,
            },
        },
    }

    mapped = stream_config_from_public(public)

    assert mapped.video_freeze_enabled is False
    assert mapped.admission_policy.startup_mode.value == "bounded_history"
    assert mapped.admission_policy.startup_lookback_segments == 4

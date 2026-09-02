from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from presentation.api.models import (
    AlertCategory,
    AlertDTO,
    AlertState,
    AudioLossCheck,
    BlackScreenCheck,
    CommandResultDTO,
    CommandResultStatusEnum,
    CommandSubmissionDTO,
    CommandTypeEnum,
    EventType,
    RuntimeStatusDTO,
    StreamChecks,
    StreamConfigDTO,
    StreamHealthEnum,
    StreamStatusEnum,
    VideoFreezeCheck,
)

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"


def test_stream_config_dto_valid():
    config = StreamConfigDTO(
        stream_id="channel-01",
        master_url="https://example.com/master.m3u8",
        checks=StreamChecks(
            black_screen=BlackScreenCheck(enabled=True),
            audio_loss=AudioLossCheck(
                enabled=True,
                threshold_dbfs=-40.0,
                duration_seconds=5.0,
                track_index=0,
            ),
        ),
    )
    data = config.model_dump(mode="json")
    assert data["schema_version"] == "1.0"
    assert data["stream_id"] == "channel-01"
    assert data["checks"]["black_screen"]["enabled"] is True
    assert data["checks"]["audio_loss"]["threshold_dbfs"] == -40.0
    assert data["checks"]["video_freeze"]["enabled"] is False


def test_stream_config_accepts_freeze_and_validates_threshold_order():
    config = StreamConfigDTO(
        stream_id="channel-01",
        master_url="https://example.com/master.m3u8",
        checks=StreamChecks(
            black_screen=BlackScreenCheck(enabled=False),
            audio_loss=AudioLossCheck(
                enabled=False,
                threshold_dbfs=-60.0,
                duration_seconds=30.0,
            ),
            video_freeze=VideoFreezeCheck(
                enabled=True,
                noise_db=-50.0,
                detector_minimum_duration=0.4,
                warning_duration_seconds=4.0,
                alert_duration_seconds=7.0,
            ),
        ),
    )

    assert config.checks.video_freeze.enabled is True
    with pytest.raises(ValidationError, match="greater than"):
        VideoFreezeCheck(
            enabled=True,
            warning_duration_seconds=5.0,
            alert_duration_seconds=5.0,
        )


@pytest.mark.parametrize("url", ["master.m3u8", "file:///tmp/master.m3u8"])
def test_stream_config_rejects_non_http_master_url(url):
    with pytest.raises(ValidationError):
        StreamConfigDTO(
            stream_id="channel-01",
            master_url=url,
            checks={
                "black_screen": {"enabled": True},
                "audio_loss": {
                    "enabled": True,
                    "threshold_dbfs": -60.0,
                    "duration_seconds": 30.0,
                },
            },
        )


def test_stream_config_dto_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        StreamConfigDTO(
            stream_id="channel-01",
            master_url="https://example.com/master.m3u8",
            storage_id="unexpected-internal-id",
            checks=StreamChecks(
                black_screen=BlackScreenCheck(enabled=True),
                audio_loss=AudioLossCheck(
                    enabled=False,
                    threshold_dbfs=-30.0,
                    duration_seconds=3.0,
                ),
            ),
        )

    with pytest.raises(ValidationError):
        StreamConfigDTO(
            stream_id="channel-01",
            master_url="https://example.com/master.m3u8",
            checks={
                "black_screen": {"enabled": True, "unexpected": True},
                "audio_loss": {
                    "enabled": False,
                    "threshold_dbfs": -30.0,
                    "duration_seconds": 3.0,
                },
            },
        )


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_stream_config_rejects_non_finite_audio_values(value):
    with pytest.raises(ValidationError):
        StreamConfigDTO(
            stream_id="channel-01",
            master_url="https://example.com/master.m3u8",
            checks={
                "black_screen": {"enabled": True},
                "audio_loss": {
                    "enabled": True,
                    "threshold_dbfs": value,
                    "duration_seconds": 3.0,
                },
            },
        )


def test_command_submission_dto_valid():
    sub = CommandSubmissionDTO(
        command_id="cmd-12345",
        stream_id="channel-01",
        status="ACCEPTED",
        message="Command accepted",
    )
    data = sub.model_dump(mode="json")
    assert data["schema_version"] == "1.0"
    assert data["command_id"] == "cmd-12345"
    assert data["status"] == "ACCEPTED"


def test_command_submission_dto_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        CommandSubmissionDTO(
            command_id="cmd-12345",
            stream_id="channel-01",
            unknown_prop="forbidden",
        )


def test_command_result_dto_valid():
    now = datetime(2026, 8, 28, 8, 0, 0, tzinfo=timezone.utc)
    result = CommandResultDTO(
        command_id="cmd-12345",
        command_type=CommandTypeEnum.START,
        stream_id="channel-01",
        status=CommandResultStatusEnum.APPLIED,
        changed=True,
        processed_at=now,
        error_code=None,
        error=None,
    )
    data = result.model_dump(mode="json")
    assert data["schema_version"] == "1.0"
    assert data["command_type"] == "START"
    assert data["status"] == "APPLIED"
    assert data["changed"] is True
    assert data["processed_at"] == "2026-08-28T08:00:00Z" or data["processed_at"].startswith("2026-08-28T08:00:00")


def test_runtime_status_dto_includes_worker_id_and_observed_at():
    now = datetime(2026, 8, 28, 8, 0, 0, tzinfo=timezone.utc)
    status = RuntimeStatusDTO(
        stream_id="channel-01",
        status=StreamStatusEnum.RUNNING,
        health=StreamHealthEnum.HEALTHY,
        started_at=now,
        last_poll_at=now,
        active_variant_count=2,
        queue_depth=3,
        queue_lag_seconds=0.5,
        checks={"black_screen": "ENABLED", "audio_loss": "DISABLED"},
        worker_id="worker.node-01",
        observed_at=now,
    )
    data = status.model_dump(mode="json")
    assert data["schema_version"] == "1.0"
    assert data["worker_id"] == "worker.node-01"
    assert data["health_reasons"] == []
    assert data["checks"] == {"black_screen": "ENABLED", "audio_loss": "DISABLED"}


def test_runtime_status_dto_worker_id_regex():
    now = datetime(2026, 8, 28, 8, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        RuntimeStatusDTO(
            stream_id="channel-01",
            status=StreamStatusEnum.RUNNING,
            health=StreamHealthEnum.HEALTHY,
            active_variant_count=1,
            queue_depth=0,
            checks={"black_screen": "ENABLED"},
            worker_id="!invalid worker id",
            observed_at=now,
        )


def test_alert_dto_default_attributes():
    now = datetime(2026, 8, 28, 8, 0, 0, tzinfo=timezone.utc)
    alert = AlertDTO(
        alert_id="alt-001",
        event_id="evt-001",
        category=AlertCategory.CONTENT,
        event_type=EventType.BLACK_SCREEN,
        state=AlertState.OPEN,
        stream_id="channel-01",
        occurred_at=now,
        emitted_at=now,
        reason="black_screen_detected",
    )
    data = alert.model_dump(mode="json")
    assert data["schema_version"] == "1.0"
    assert data["attributes"] == {}
    assert data["event_type"] == "BLACK_SCREEN"
    assert data["state"] == "OPEN"

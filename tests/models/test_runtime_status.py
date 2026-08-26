from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from models.runtime_status import (
    CheckStatus,
    PublicStreamStatus,
    RuntimeHealth,
    RuntimeStatus,
)

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"


def load_schema(name: str) -> dict:
    with (CONTRACT_ROOT / name).open(encoding="utf-8") as f:
        return json.load(f)


def test_runtime_status_to_dict_matches_public_contract_fields():
    schema = load_schema("runtime-status.schema.json")
    now = datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc)
    status = RuntimeStatus(
        stream_id="channel-01",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        started_at=now,
        last_poll_at=now,
        active_variant_count=3,
        queue_depth=5,
        queue_lag_seconds=1.25,
        error=None,
        telemetry_available=True,
        health_reasons=("all_checks_passing",),
        checks={
            "black_screen": CheckStatus.ENABLED,
            "audio_loss": CheckStatus.DISABLED,
        },
    )

    data = status.to_dict()
    assert not hasattr(status, "storage_id")
    assert "storage_id" not in data
    assert data["schema_version"] == schema["properties"]["schema_version"]["const"]
    assert data["stream_id"] == "channel-01"
    assert data["status"] in schema["properties"]["status"]["enum"]
    assert data["health"] in schema["properties"]["health"]["enum"]
    assert data["started_at"] == "2026-08-26T10:00:00+00:00"
    assert data["last_poll_at"] == "2026-08-26T10:00:00+00:00"
    assert data["active_variant_count"] == 3
    assert data["queue_depth"] == 5
    assert data["queue_lag_seconds"] == 1.25
    assert data["telemetry_available"] is True
    assert data["health_reasons"] == ["all_checks_passing"]
    assert data["checks"] == {
        "black_screen": "ENABLED",
        "audio_loss": "DISABLED",
    }

    # Verify required keys in schema are present in data
    for req in schema["required"]:
        assert req in data


def test_runtime_status_with_null_and_error_fields():
    schema = load_schema("runtime-status.schema.json")
    status = RuntimeStatus(
        stream_id="channel-02",
        status=PublicStreamStatus.FAILED,
        health=RuntimeHealth.UNHEALTHY,
        started_at=None,
        last_poll_at=None,
        active_variant_count=0,
        queue_depth=0,
        queue_lag_seconds=None,
        error="session failed with ffmpeg error",
        telemetry_available=False,
        health_reasons=(),
        checks={
            "black_screen": CheckStatus.ENABLED,
            "audio_loss": CheckStatus.ENABLED,
        },
    )

    data = status.to_dict()
    assert data["started_at"] is None
    assert data["last_poll_at"] is None
    assert data["queue_lag_seconds"] is None
    assert data["error"] == "session failed with ffmpeg error"
    assert data["telemetry_available"] is False
    assert data["health_reasons"] == []

    for req in schema["required"]:
        assert req in data


def test_runtime_status_normalizes_timestamps_to_utc_and_freezes_checks():
    local_time = datetime.fromisoformat("2026-08-26T17:00:00+07:00")
    checks = {"black_screen": CheckStatus.ENABLED}
    status = RuntimeStatus(
        stream_id="channel-03",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks=checks,
        started_at=local_time,
    )

    checks["black_screen"] = CheckStatus.DISABLED

    assert status.to_dict()["started_at"] == "2026-08-26T10:00:00+00:00"
    assert status.checks["black_screen"] is CheckStatus.ENABLED
    with pytest.raises(TypeError):
        status.checks["audio_loss"] = CheckStatus.ENABLED


@pytest.mark.parametrize("invalid_lag", [float("nan"), float("inf"), -1.0])
def test_runtime_status_rejects_invalid_queue_lag(invalid_lag):
    with pytest.raises(ValueError):
        RuntimeStatus(
            stream_id="channel-04",
            status=PublicStreamStatus.RUNNING,
            health=RuntimeHealth.UNKNOWN,
            active_variant_count=0,
            queue_depth=0,
            queue_lag_seconds=invalid_lag,
            checks={},
        )

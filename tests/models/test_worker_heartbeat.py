from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from models.worker_heartbeat import WorkerHeartbeat, WorkerState

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"


def load_schema(name: str) -> dict:
    with (CONTRACT_ROOT / name).open(encoding="utf-8") as f:
        return json.load(f)


def test_worker_heartbeat_serialization_matches_public_schema():
    schema = load_schema("worker-heartbeat.schema.json")
    t_start = datetime(2026, 8, 27, 9, 0, 0, tzinfo=timezone.utc)
    t_seen = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)

    hb = WorkerHeartbeat(
        worker_id="worker-local-01",
        state=WorkerState.READY,
        started_at=t_start,
        last_seen_at=t_seen,
        command_consumer_ready=True,
        active_stream_count=1,
        max_streams=4,
        active_media_processes=2,
        max_media_processes=4,
        version="v1.0.0",
    )

    data = hb.to_dict()
    assert data["schema_version"] == "1.0"
    assert data["worker_id"] == "worker-local-01"
    assert data["state"] == "READY"
    assert data["started_at"] == "2026-08-27T09:00:00+00:00"
    assert data["last_seen_at"] == "2026-08-27T10:00:00+00:00"
    assert data["command_consumer_ready"] is True
    assert data["active_stream_count"] == 1
    assert data["max_streams"] == 4
    assert data["active_media_processes"] == 2
    assert data["max_media_processes"] == 4
    assert data["version"] == "v1.0.0"

    # Verify no secret or internal leakage
    assert "storage_id" not in data
    assert "redis" not in str(data).lower()

    for req in schema["required"]:
        assert req in data


@pytest.mark.parametrize("state", list(WorkerState))
def test_worker_heartbeat_all_states_valid(state):
    t_start = datetime(2026, 8, 27, 9, 0, 0, tzinfo=timezone.utc)
    hb = WorkerHeartbeat(
        worker_id="worker-01",
        state=state,
        started_at=t_start,
        last_seen_at=t_start,
        command_consumer_ready=False,
        active_stream_count=0,
        max_streams=1,
        active_media_processes=0,
        max_media_processes=1,
        version="dev",
    )
    assert hb.state == state


def test_worker_heartbeat_rejects_invalid_values():
    t_start = datetime(2026, 8, 27, 9, 0, 0, tzinfo=timezone.utc)
    t_seen = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)

    # Invalid worker_id
    with pytest.raises(ValueError, match="Invalid worker_id"):
        WorkerHeartbeat(
            worker_id="invalid@worker",
            state=WorkerState.READY,
            started_at=t_start,
            last_seen_at=t_seen,
            command_consumer_ready=True,
            active_stream_count=0,
            max_streams=4,
            active_media_processes=0,
            max_media_processes=4,
            version="1.0",
        )

    # started_at > last_seen_at
    with pytest.raises(ValueError, match="started_at must be <= last_seen_at"):
        WorkerHeartbeat(
            worker_id="worker-01",
            state=WorkerState.READY,
            started_at=t_seen,
            last_seen_at=t_start,
            command_consumer_ready=True,
            active_stream_count=0,
            max_streams=4,
            active_media_processes=0,
            max_media_processes=4,
            version="1.0",
        )

    # active_stream_count > max_streams
    with pytest.raises(ValueError, match="active_stream_count cannot exceed max_streams"):
        WorkerHeartbeat(
            worker_id="worker-01",
            state=WorkerState.READY,
            started_at=t_start,
            last_seen_at=t_seen,
            command_consumer_ready=True,
            active_stream_count=5,
            max_streams=4,
            active_media_processes=0,
            max_media_processes=4,
            version="1.0",
        )

    # negative counter
    with pytest.raises(ValueError, match="active_media_processes must be >= 0"):
        WorkerHeartbeat(
            worker_id="worker-01",
            state=WorkerState.READY,
            started_at=t_start,
            last_seen_at=t_seen,
            command_consumer_ready=True,
            active_stream_count=0,
            max_streams=4,
            active_media_processes=-1,
            max_media_processes=4,
            version="1.0",
        )

    # empty version
    with pytest.raises(ValueError, match="version must be a non-empty string"):
        WorkerHeartbeat(
            worker_id="worker-01",
            state=WorkerState.READY,
            started_at=t_start,
            last_seen_at=t_seen,
            command_consumer_ready=True,
            active_stream_count=0,
            max_streams=4,
            active_media_processes=0,
            max_media_processes=4,
            version="   ",
        )

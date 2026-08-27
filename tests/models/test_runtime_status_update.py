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
from models.runtime_status_update import (
    RuntimeStatusUpdate,
    RuntimeStatusUpdateType,
)

CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts"


def load_schema(name: str) -> dict:
    with (CONTRACT_ROOT / name).open(encoding="utf-8") as f:
        return json.load(f)


def test_snapshot_update_serialization_matches_schema():
    schema = load_schema("runtime-status-update.schema.json")
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    status = RuntimeStatus(
        stream_id="channel-01",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=2,
        queue_depth=0,
        checks={"black_screen": CheckStatus.ENABLED},
        worker_id="worker-local-01",
        observed_at=now,
    )
    update = RuntimeStatusUpdate(
        update_id="update-001",
        update_type=RuntimeStatusUpdateType.SNAPSHOT,
        stream_id="channel-01",
        worker_id="worker-local-01",
        observed_at=now,
        status=status,
    )

    data = update.to_dict()
    assert data["schema_version"] == "1.0"
    assert data["update_id"] == "update-001"
    assert data["update_type"] == "SNAPSHOT"
    assert data["stream_id"] == "channel-01"
    assert data["worker_id"] == "worker-local-01"
    assert data["observed_at"] == "2026-08-27T10:00:00+00:00"
    assert "status" in data
    assert data["status"]["stream_id"] == "channel-01"
    assert data["status"]["worker_id"] == "worker-local-01"

    for req in schema["required"]:
        assert req in data


def test_removed_update_serialization_matches_schema():
    schema = load_schema("runtime-status-update.schema.json")
    now = datetime(2026, 8, 27, 10, 15, 0, tzinfo=timezone.utc)
    update = RuntimeStatusUpdate(
        update_id="update-002",
        update_type=RuntimeStatusUpdateType.REMOVED,
        stream_id="channel-01",
        worker_id="worker-local-01",
        observed_at=now,
        status=None,
    )

    data = update.to_dict()
    assert data["schema_version"] == "1.0"
    assert data["update_id"] == "update-002"
    assert data["update_type"] == "REMOVED"
    assert data["stream_id"] == "channel-01"
    assert data["worker_id"] == "worker-local-01"
    assert data["observed_at"] == "2026-08-27T10:15:00+00:00"
    assert "status" not in data

    for req in schema["required"]:
        assert req in data


def test_snapshot_requires_status_and_matching_identifiers():
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)

    # Missing status in SNAPSHOT
    with pytest.raises(ValueError, match="SNAPSHOT update requires a valid RuntimeStatus"):
        RuntimeStatusUpdate(
            update_id="update-003",
            update_type=RuntimeStatusUpdateType.SNAPSHOT,
            stream_id="channel-01",
            worker_id="worker-local-01",
            observed_at=now,
            status=None,
        )

    # Mismatched stream_id
    status_other_stream = RuntimeStatus(
        stream_id="channel-02",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
        worker_id="worker-local-01",
        observed_at=now,
    )
    with pytest.raises(ValueError, match="does not match update stream_id"):
        RuntimeStatusUpdate(
            update_id="update-004",
            update_type=RuntimeStatusUpdateType.SNAPSHOT,
            stream_id="channel-01",
            worker_id="worker-local-01",
            observed_at=now,
            status=status_other_stream,
        )

    # Mismatched worker_id
    status_other_worker = RuntimeStatus(
        stream_id="channel-01",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=1,
        queue_depth=0,
        checks={},
        worker_id="worker-local-02",
        observed_at=now,
    )
    with pytest.raises(ValueError, match="does not match update worker_id"):
        RuntimeStatusUpdate(
            update_id="update-005",
            update_type=RuntimeStatusUpdateType.SNAPSHOT,
            stream_id="channel-01",
            worker_id="worker-local-01",
            observed_at=now,
            status=status_other_worker,
        )


def test_removed_must_not_contain_status():
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    status = RuntimeStatus(
        stream_id="channel-01",
        status=PublicStreamStatus.STOPPED,
        health=RuntimeHealth.UNKNOWN,
        active_variant_count=0,
        queue_depth=0,
        checks={},
        worker_id="worker-local-01",
        observed_at=now,
    )
    with pytest.raises(ValueError, match="REMOVED update must not contain a status payload"):
        RuntimeStatusUpdate(
            update_id="update-006",
            update_type=RuntimeStatusUpdateType.REMOVED,
            stream_id="channel-01",
            worker_id="worker-local-01",
            observed_at=now,
            status=status,
        )


@pytest.mark.parametrize("invalid_worker_id", ["", "-invalid", ".invalid", "worker@node"])
def test_invalid_worker_id_rejected_in_update(invalid_worker_id):
    now = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="Invalid worker_id"):
        RuntimeStatusUpdate(
            update_id="update-007",
            update_type=RuntimeStatusUpdateType.REMOVED,
            stream_id="channel-01",
            worker_id=invalid_worker_id,
            observed_at=now,
        )

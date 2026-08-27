from datetime import datetime, timezone
import json
import os
from uuid import uuid4

import pytest

from app.redis_runtime_status_projector import RedisRuntimeStatusProjector
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import PublicRuntimeRedisKeys, RedisNamespace
from models.runtime_status import (
    CheckStatus,
    PublicStreamStatus,
    RuntimeHealth,
    RuntimeStatus,
)
from models.runtime_status_update import RuntimeStatusUpdateType

pytestmark = pytest.mark.redis_integration


@pytest.fixture
def redis_context():
    client = RedisClient(
        RedisSettings(
            url=os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15"),
            socket_connect_timeout=0.25,
            socket_timeout=1.0,
        )
    )
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Disposable Redis is unavailable: {exc}")
    namespace = RedisNamespace(f"media-monitor:test:{uuid4().hex}")
    try:
        yield client, namespace
    finally:
        found = list(client.client.scan_iter(match=f"{namespace.prefix}:*"))
        if found:
            client.client.delete(*found)
        client.close()


def test_redis_runtime_status_projection_lifecycle(redis_context):
    client, namespace = redis_context
    keys = PublicRuntimeRedisKeys(namespace)
    projector = RedisRuntimeStatusProjector(redis_client=client, keys=keys)

    t1 = datetime(2026, 8, 27, 10, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 27, 10, 0, 2, tzinfo=timezone.utc)
    t3 = datetime(2026, 8, 27, 10, 0, 4, tzinfo=timezone.utc)
    t4 = datetime(2026, 8, 27, 10, 10, 0, tzinfo=timezone.utc)

    # 1. First Snapshot
    status_v1 = RuntimeStatus(
        stream_id="channel-integration-01",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=2,
        queue_depth=0,
        checks={"black_screen": CheckStatus.ENABLED},
        worker_id="worker-integration-01",
        observed_at=t1,
        last_poll_at=t1,
    )
    assert projector.project(status_v1) is True

    # Check Hash
    raw_hash = client.client.hget(keys.current_statuses(), "channel-integration-01")
    assert raw_hash is not None
    hash_data = json.loads(raw_hash)
    assert hash_data["stream_id"] == "channel-integration-01"
    assert hash_data["worker_id"] == "worker-integration-01"
    assert hash_data["health"] == "HEALTHY"

    # Check Stream
    updates = client.client.xrange(keys.status_updates())
    assert len(updates) == 1
    update_1 = json.loads(updates[0][1][b"payload" if b"payload" in updates[0][1] else "payload"])
    assert update_1["update_type"] == RuntimeStatusUpdateType.SNAPSHOT.value
    assert update_1["stream_id"] == "channel-integration-01"

    # 2. Duplicate snapshot (only observed_at changes)
    status_v2 = RuntimeStatus(
        stream_id="channel-integration-01",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.HEALTHY,
        active_variant_count=2,
        queue_depth=0,
        checks={"black_screen": CheckStatus.ENABLED},
        worker_id="worker-integration-01",
        observed_at=t2,
        last_poll_at=t1,
    )
    assert projector.project(status_v2) is False

    # Stream length must remain 1
    assert client.client.xlen(keys.status_updates()) == 1
    # Hash observed_at is updated
    raw_hash = client.client.hget(keys.current_statuses(), "channel-integration-01")
    assert json.loads(raw_hash)["observed_at"] == "2026-08-27T10:00:02+00:00"

    # 3. Semantic change (Health becomes DEGRADED)
    status_v3 = RuntimeStatus(
        stream_id="channel-integration-01",
        status=PublicStreamStatus.RUNNING,
        health=RuntimeHealth.DEGRADED,
        active_variant_count=2,
        queue_depth=1,
        checks={"black_screen": CheckStatus.ENABLED},
        worker_id="worker-integration-01",
        observed_at=t3,
        last_poll_at=t3,
    )
    assert projector.project(status_v3) is True
    assert client.client.xlen(keys.status_updates()) == 2

    # 4. Remove
    assert projector.remove(
        stream_id="channel-integration-01",
        worker_id="worker-integration-01",
        observed_at=t4,
    ) is True

    # Hash entry deleted
    assert client.client.hget(keys.current_statuses(), "channel-integration-01") is None
    # Stream length is now 3
    updates = client.client.xrange(keys.status_updates())
    assert len(updates) == 3
    last_update = json.loads(updates[-1][1][b"payload" if b"payload" in updates[-1][1] else "payload"])
    assert last_update["update_type"] == RuntimeStatusUpdateType.REMOVED.value
    assert last_update["stream_id"] == "channel-integration-01"
    assert "status" not in last_update

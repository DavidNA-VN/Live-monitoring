from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4

import pytest

from app.redis_worker_heartbeat_publisher import RedisWorkerHeartbeatPublisher
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import RedisNamespace, WorkerRedisKeys
from models.worker_heartbeat import WorkerHeartbeat, WorkerState

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


def test_redis_worker_heartbeat_lifecycle(redis_context):
    client, namespace = redis_context
    keys = WorkerRedisKeys(namespace)
    publisher = RedisWorkerHeartbeatPublisher(redis_client=client, keys=keys)

    now = datetime.now(timezone.utc)
    hb_ready = WorkerHeartbeat(
        worker_id="worker-integration-01",
        state=WorkerState.READY,
        started_at=now,
        last_seen_at=now,
        command_consumer_ready=True,
        active_stream_count=1,
        max_streams=4,
        active_media_processes=2,
        max_media_processes=4,
        version="v1.0.0",
    )

    # 1. Publish Heartbeat with short TTL (2 seconds)
    publisher.publish(hb_ready, ttl_seconds=2, discovery_window_seconds=10)

    # Verify Heartbeat key exists and matches
    raw = client.client.get(keys.heartbeat("worker-integration-01"))
    assert raw is not None
    data = json.loads(raw)
    assert data["worker_id"] == "worker-integration-01"
    assert data["state"] == "READY"
    assert data["active_stream_count"] == 1

    # Verify Discovery ZSET
    members = client.client.zrangebyscore(keys.active_workers(), "-inf", "+inf")
    assert any(
        m == b"worker-integration-01" or m == "worker-integration-01"
        for m in members
    )

    # 2. Wait for TTL to expire
    time.sleep(2.1)
    expired = client.client.get(keys.heartbeat("worker-integration-01"))
    assert expired is None  # Worker is considered dead because TTL expired

    # 3. Publish STOPPING state
    now_stopping = datetime.now(timezone.utc)
    hb_stopping = WorkerHeartbeat(
        worker_id="worker-integration-01",
        state=WorkerState.STOPPING,
        started_at=now,
        last_seen_at=now_stopping,
        command_consumer_ready=False,
        active_stream_count=0,
        max_streams=4,
        active_media_processes=0,
        max_media_processes=4,
        version="v1.0.0",
    )
    publisher.publish(hb_stopping, ttl_seconds=5, discovery_window_seconds=10)

    raw_stopping = client.client.get(keys.heartbeat("worker-integration-01"))
    assert raw_stopping is not None
    assert json.loads(raw_stopping)["state"] == "STOPPING"

from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4

import pytest

from app.monitoring_worker import MonitoringWorkerApplication
from app.redis_desired_state_repository import RedisDesiredStateRepository
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import DesiredStateRedisKeys, RedisNamespace
from core.stream_session import StreamSessionStatus
from models.desired_stream_state import (
    DesiredLifecycleState,
    DesiredStreamState,
)
from models.stream_config import StreamConfig

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


def make_config(stream_id="channel-01"):
    return StreamConfig(
        stream_id=stream_id,
        master_url="https://example.test/live.m3u8",
        black_screen_enabled=True,
        audio_loss_enabled=False,
    )


def test_real_redis_desired_state_restart_recovery(redis_context):
    client, namespace = redis_context
    desired_keys = DesiredStateRedisKeys(namespace)
    repo = RedisDesiredStateRepository(redis_client=client, keys=desired_keys)

    now = datetime.now(timezone.utc)
    # Pre-populate Desired States in Redis Hash before worker starts
    repo.save(
        DesiredStreamState(
            stream_id="channel-running",
            desired_state=DesiredLifecycleState.RUNNING,
            updated_at=now,
            config=make_config("channel-running"),
        )
    )
    repo.save(
        DesiredStreamState(
            stream_id="channel-paused",
            desired_state=DesiredLifecycleState.PAUSED,
            updated_at=now,
            config=make_config("channel-paused"),
        )
    )
    repo.save(
        DesiredStreamState(
            stream_id="channel-stopped",
            desired_state=DesiredLifecycleState.STOPPED,
            updated_at=now,
            config=None,
        )
    )

    # Corrupted entry
    client.client.hset(desired_keys.current_states(), "channel-corrupt", "{invalid json")

    # Start new worker instance (simulating restart)
    application = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        max_streams=10,
        worker_id="worker-recovery-01",
    )
    try:
        # Perform reconciliation
        report = application.reconcile()
        assert report.total_records == 3
        assert report.running_started == 1
        assert report.paused_registered == 1
        assert report.stopped_skipped == 1

        # Check in-memory states
        snap_running = application.supervisor.snapshot("channel-running")
        assert snap_running is not None
        assert snap_running.status == StreamSessionStatus.RUNNING

        snap_paused = application.supervisor.snapshot("channel-paused")
        assert snap_paused is not None
        assert snap_paused.status == StreamSessionStatus.PAUSED

        assert application.supervisor.snapshot("channel-stopped") is None

        # Verify corrupted record was quarantined in Redis Stream
        quarantine_entries = client.client.xrange(desired_keys.recovery_errors())
        assert len(quarantine_entries) >= 1
        assert client.client.hget(
            desired_keys.current_states(), "channel-corrupt"
        ) is None
    finally:
        application.close()

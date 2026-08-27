from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4

import pytest

from app.monitoring_worker import MonitoringWorkerApplication
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import ControlRedisKeys, RedisNamespace
from core.stream_session import StreamSessionStatus
from models.monitoring_command import MonitoringCommand, MonitoringCommandResultStatus

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


def make_command_payload(command_id="cmd-1", stream_id="channel-01", action="START"):
    config = {
        "schema_version": "1.0",
        "stream_id": stream_id,
        "master_url": "https://example.test/live.m3u8",
        "checks": {
            "black_screen": {"enabled": True},
            "audio_loss": {
                "enabled": True,
                "threshold_dbfs": -60,
                "duration_seconds": 30,
                "track_index": 0,
            },
        },
    } if action in ("START", "UPDATE_CONFIG") else None

    return json.dumps({
        "schema_version": "1.0",
        "command_id": command_id,
        "command_type": action,
        "stream_id": stream_id,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
    })


def test_real_redis_own_pending_recovery(redis_context):
    client, namespace = redis_context
    control_keys = ControlRedisKeys(namespace)

    # 1. Create consumer group and post a command
    try:
        client.client.xgroup_create(
            control_keys.commands(),
            "monitoring-workers",
            id="0-0",
            mkstream=True,
        )
    except Exception:
        pass

    entry_id = client.client.xadd(
        control_keys.commands(),
        {"payload": make_command_payload("cmd-pending-1", "channel-pending")},
    )

    # Read the entry under consumer-1 but DO NOT acknowledge it (simulating crash before ACK)
    read_resp = client.client.xreadgroup(
        "monitoring-workers",
        "worker-01-commands",
        {control_keys.commands(): ">"},
        count=1,
    )
    assert len(read_resp[0][1]) == 1

    # Post a newer command
    entry_id_new = client.client.xadd(
        control_keys.commands(),
        {"payload": make_command_payload("cmd-new-2", "channel-new")},
    )

    # Start worker with same consumer name (simulating restart)
    application = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        consumer_name="worker-01-commands",
        worker_id="worker-01",
    )
    try:
        # First poll should read own pending command (cmd-pending-1)
        processed = application.command_consumer.poll_once()
        assert processed == 1
        assert application.supervisor.snapshot("channel-pending") is not None
        assert application.supervisor.snapshot("channel-new") is None

        # Second poll should read the new command (cmd-new-2)
        processed = application.command_consumer.poll_once()
        assert processed == 1
        assert application.supervisor.snapshot("channel-new") is not None
    finally:
        application.close()


def test_real_redis_abandoned_pending_recovery_via_autoclaim(redis_context):
    client, namespace = redis_context
    control_keys = ControlRedisKeys(namespace)

    try:
        client.client.xgroup_create(
            control_keys.commands(),
            "monitoring-workers",
            id="0-0",
            mkstream=True,
        )
    except Exception:
        pass

    # Dead worker reads command but dies
    client.client.xadd(
        control_keys.commands(),
        {"payload": make_command_payload("cmd-orphaned-1", "channel-orphaned")},
    )
    client.client.xreadgroup(
        "monitoring-workers",
        "dead-worker-commands",
        {control_keys.commands(): ">"},
        count=1,
    )

    # Start replacement worker with claim_idle_milliseconds = 0
    application = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        consumer_name="new-worker-commands",
        worker_id="worker-new-01",
    )
    application.command_consumer.claim_idle_milliseconds = 0

    try:
        processed = application.command_consumer.poll_once()
        assert processed == 1
        assert application.supervisor.snapshot("channel-orphaned") is not None
    finally:
        application.close()


def test_unclaimable_orphan_pending_blocks_new_delivery(redis_context):
    client, namespace = redis_context
    keys = ControlRedisKeys(namespace)
    client.client.xgroup_create(
        keys.commands(),
        "monitoring-workers",
        id="0-0",
        mkstream=True,
    )
    client.client.xadd(
        keys.commands(),
        {"payload": make_command_payload("cmd-old", "channel-old")},
    )
    client.client.xreadgroup(
        "monitoring-workers",
        "dead-worker",
        {keys.commands(): ">"},
        count=1,
    )
    client.client.xadd(
        keys.commands(),
        {"payload": make_command_payload("cmd-new", "channel-new")},
    )

    application = MonitoringWorkerApplication(
        redis_settings=client.settings,
        namespace=namespace,
        consumer_name="replacement-worker",
        worker_id="replacement-worker",
    )
    try:
        application.command_consumer.claim_idle_milliseconds = 60_000
        assert application.command_consumer.poll_once() == 0
        assert application.supervisor.snapshot("channel-new") is None

        application.command_consumer.claim_idle_milliseconds = 0
        assert application.command_consumer.poll_once() == 1
        assert application.supervisor.snapshot("channel-old") is not None
        assert application.supervisor.snapshot("channel-new") is None
    finally:
        application.close()

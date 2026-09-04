from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
import redis.asyncio as aioredis

from core.alert_stream import RedisAlertStream
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import AlertRedisKeys, ControlRedisKeys, PublicRuntimeRedisKeys, RedisNamespace, RuntimeRedisKeys
from models.alert import AlertCategory, AlertEnvelope
from presentation.api.adapters.redis_alert_source import RedisAlertSource
from presentation.api.adapters.redis_command_result_reader import RedisCommandResultReader
from presentation.api.adapters.redis_monitoring_control import RedisMonitoringControl
from presentation.api.adapters.redis_runtime_status import RedisRuntimeStatusReader
from presentation.api.main import create_app

pytestmark = pytest.mark.redis_integration


@pytest.fixture
def redis_alert_context():
    redis_url = os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15")
    sync_client = RedisClient(
        RedisSettings(
            url=redis_url,
            socket_connect_timeout=0.25,
            socket_timeout=1.0,
        )
    )
    try:
        sync_client.ping()
    except Exception as exc:
        sync_client.close()
        pytest.skip(f"Disposable Redis is unavailable: {exc}")

    async_client = aioredis.from_url(redis_url, decode_responses=False)
    namespace = RedisNamespace(f"media-monitor:test:{uuid4().hex}")
    alert_keys = AlertRedisKeys(namespace)
    control_keys = ControlRedisKeys(namespace)
    public_keys = PublicRuntimeRedisKeys(namespace)
    runtime_keys = RuntimeRedisKeys(namespace)

    try:
        yield sync_client, async_client, alert_keys, control_keys, public_keys, runtime_keys, namespace
    finally:
        found = list(sync_client.client.scan_iter(match=f"{namespace.prefix}:*"))
        if found:
            sync_client.client.delete(*found)
        sync_client.close()


def test_tier1_history_rest_endpoint(redis_alert_context):
    sync_client, async_client, alert_keys, control_keys, public_keys, runtime_keys, namespace = redis_alert_context

    alert_source = RedisAlertSource(
        redis_client=async_client,
        keys=alert_keys,
    )
    control = RedisMonitoringControl(redis_client=async_client, keys=control_keys)
    cmd_reader = RedisCommandResultReader(redis_client=async_client, keys=control_keys)
    status_reader = RedisRuntimeStatusReader(redis_client=async_client, keys=public_keys)

    app = create_app(
        control=control,
        command_results=cmd_reader,
        status_reader=status_reader,
        alert_source=alert_source,
        enable_fake_generator=False,
    )

    t0 = datetime(2026, 8, 28, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 8, 28, 10, 1, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 28, 10, 2, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 8, 28, 10, 3, 0, tzinfo=timezone.utc)

    # 1. Add alert 1 for chan-01 (BLACK_SCREEN OPEN)
    env1 = AlertEnvelope(
        alert_id="a-1",
        event_id="evt-black-01",
        category=AlertCategory.CONTENT,
        event_type="BLACK_SCREEN",
        state="OPEN",
        stream_id="chan-01",
        occurred_at=t0,
        emitted_at=t0,
        reason="Black screen detected",
        variant_stable_id="var-1080p",
        event_started_at=t0,
        attributes={"severity": "CRITICAL"},
    )
    sync_client.client.xadd(alert_keys.outbox(), env1.to_redis_fields())

    # 2. Add alert for chan-02 (OTHER stream)
    env_other = AlertEnvelope(
        alert_id="a-other",
        event_id="evt-other-01",
        category=AlertCategory.CONTENT,
        event_type="BLACK_SCREEN",
        state="OPEN",
        stream_id="chan-02",
        occurred_at=t1,
        emitted_at=t1,
        reason="Other stream alert",
    )
    sync_client.client.xadd(alert_keys.outbox(), env_other.to_redis_fields())

    # 3. Add alert 2 for chan-01 (BLACK_SCREEN RESOLVED)
    env2 = AlertEnvelope(
        alert_id="a-2",
        event_id="evt-black-01",
        category=AlertCategory.CONTENT,
        event_type="BLACK_SCREEN",
        state="RESOLVED",
        stream_id="chan-01",
        occurred_at=t2,
        emitted_at=t2,
        reason="Black screen recovered",
        variant_stable_id="var-1080p",
        event_started_at=t0,
        event_ended_at=t2,
        attributes={"severity": "RESOLVED"},
    )
    sync_client.client.xadd(alert_keys.outbox(), env2.to_redis_fields())

    # 4. Add alert 3 for chan-01 (AUDIO_LOSS OPEN)
    env3 = AlertEnvelope(
        alert_id="a-3",
        event_id="evt-audio-01",
        category=AlertCategory.CONTENT,
        event_type="AUDIO_LOSS",
        state="OPEN",
        stream_id="chan-01",
        occurred_at=t3,
        emitted_at=t3,
        reason="Audio loss detected",
        attributes={"threshold_dbfs": "-35.0"},
    )
    sync_client.client.xadd(alert_keys.outbox(), env3.to_redis_fields())

    with TestClient(app) as client:
        res = client.get("/api/v1/streams/chan-01/events?limit=50")
        assert res.status_code == 200
        events = res.json()
        assert len(events) == 3

        # Chronological order: a-1 -> a-2 -> a-3
        assert events[0]["alert_id"] == "a-1"
        assert events[0]["event_type"] == "BLACK_SCREEN"
        assert events[0]["state"] == "OPEN"
        assert events[0]["variant_stable_id"] == "var-1080p"
        assert events[0]["attributes"] == {"severity": "CRITICAL"}

        assert events[1]["alert_id"] == "a-2"
        assert events[1]["event_id"] == "evt-black-01"  # Same event_id as a-1
        assert events[1]["state"] == "RESOLVED"
        assert events[1]["event_ended_at"] is not None

        assert events[2]["alert_id"] == "a-3"
        assert events[2]["event_type"] == "AUDIO_LOSS"
        assert events[2]["state"] == "OPEN"


def test_tier2_tier3_websocket_fanout_and_disease_types(redis_alert_context):
    sync_client, async_client, alert_keys, control_keys, public_keys, runtime_keys, namespace = redis_alert_context

    alert_source = RedisAlertSource(
        redis_client=async_client,
        keys=alert_keys,
        xread_block_milliseconds=50,
    )
    control = RedisMonitoringControl(redis_client=async_client, keys=control_keys)
    cmd_reader = RedisCommandResultReader(redis_client=async_client, keys=control_keys)
    status_reader = RedisRuntimeStatusReader(redis_client=async_client, keys=public_keys)

    app = create_app(
        control=control,
        command_results=cmd_reader,
        status_reader=status_reader,
        alert_source=alert_source,
        enable_fake_generator=False,
    )

    now = datetime.now(timezone.utc)

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/streams/chan-ws") as ws_a:
            with client.websocket_connect("/api/v1/ws/streams/chan-ws") as ws_b:
                # TestClient completes the WebSocket handshake before the
                # subscription coroutine necessarily captures its Redis cursor.
                time.sleep(0.1)
                # Both clients connected. Now publish BLACK_SCREEN alert
                env_black = AlertEnvelope(
                    alert_id="ws-black-01",
                    event_id="evt-bs-1",
                    category=AlertCategory.CONTENT,
                    event_type="BLACK_SCREEN",
                    state="OPEN",
                    stream_id="chan-ws",
                    occurred_at=now,
                    emitted_at=now,
                    reason="Black screen detected",
                    attributes={"duration": "5.0"},
                )
                sync_client.client.xadd(alert_keys.outbox(), env_black.to_redis_fields())

                # Client A receives
                msg_a1 = ws_a.receive_json()
                assert msg_a1["message_type"] == "ALERT"
                assert msg_a1["stream_id"] == "chan-ws"
                assert msg_a1["payload"]["alert_id"] == "ws-black-01"
                assert msg_a1["payload"]["event_type"] == "BLACK_SCREEN"
                assert msg_a1["payload"]["state"] == "OPEN"

                # Client B receives (fan-out)
                msg_b1 = ws_b.receive_json()
                assert msg_b1["message_type"] == "ALERT"
                assert msg_b1["stream_id"] == "chan-ws"
                assert msg_b1["payload"]["alert_id"] == "ws-black-01"
                assert msg_b1["payload"]["event_type"] == "BLACK_SCREEN"

                # Now publish AUDIO_LOSS alert
                env_audio = AlertEnvelope(
                    alert_id="ws-audio-01",
                    event_id="evt-al-1",
                    category=AlertCategory.CONTENT,
                    event_type="AUDIO_LOSS",
                    state="OPEN",
                    stream_id="chan-ws",
                    occurred_at=now,
                    emitted_at=now,
                    reason="Audio loss detected",
                    attributes={"threshold_dbfs": "-40.0"},
                )
                sync_client.client.xadd(alert_keys.outbox(), env_audio.to_redis_fields())

                # Client A receives AUDIO_LOSS
                msg_a2 = ws_a.receive_json()
                assert msg_a2["payload"]["alert_id"] == "ws-audio-01"
                assert msg_a2["payload"]["event_type"] == "AUDIO_LOSS"

                # Client B receives AUDIO_LOSS
                msg_b2 = ws_b.receive_json()
                assert msg_b2["payload"]["alert_id"] == "ws-audio-01"
                assert msg_b2["payload"]["event_type"] == "AUDIO_LOSS"

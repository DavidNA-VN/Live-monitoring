from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest

from core.alert_stream import RedisAlertStream
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from core.runtime_health import (
    RedisRuntimeHealthReporter,
    runtime_health_event_id,
)
from models.alert import AlertCategory, AlertEnvelope
from models.runtime import LiveCycleStats
from presentation.api.adapters.alert_codec import parse_alert_fields


pytestmark = pytest.mark.redis_integration


@pytest.fixture
def redis_context():
    client = RedisClient(
        RedisSettings(
            url=os.getenv(
                "REDIS_TEST_URL", "redis://localhost:6379/15"
            ),
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
    alert_keys = AlertRedisKeys(namespace)
    runtime_keys = RuntimeRedisKeys(namespace)
    try:
        yield client, alert_keys, runtime_keys
    finally:
        found = list(
            client.client.scan_iter(match=f"{namespace.prefix}:*")
        )
        if found:
            client.client.delete(*found)
        client.close()


def test_alert_stream_is_bounded_and_schema_is_consumable(redis_context):
    client, alert_keys, runtime_keys = redis_context
    stream = RedisAlertStream(
        storage_id="storage-1",
        alert_keys=alert_keys,
        runtime_keys=runtime_keys,
        max_length=5,
    )
    now = datetime.now(timezone.utc)

    for index in range(12):
        pipeline = client.client.pipeline(transaction=True)
        stream.append(
            pipeline,
            AlertEnvelope(
                alert_id=f"alert-{index}",
                event_id=f"event-{index}",
                category=AlertCategory.CONTENT,
                event_type="BLACK_SCREEN",
                state="OPEN",
                stream_id="channel-01",
                occurred_at=now,
                emitted_at=now,
                reason="test",
            ),
        )
        pipeline.execute()

    assert client.client.xlen(alert_keys.outbox()) == 5
    _, latest = client.client.xrevrange(
        alert_keys.outbox(), count=1
    )[0]
    decoded_latest = AlertEnvelope.from_redis_fields(latest)
    assert decoded_latest.event_id == "event-11"
    assert decoded_latest.stream_id == "channel-01"
    metrics = client.client.hgetall(runtime_keys.metrics("storage-1"))
    assert metrics["alert_total"] == "12"
    assert metrics["alert_content_total"] == "12"


def test_runtime_health_uses_same_versioned_envelope(redis_context):
    client, alert_keys, runtime_keys = redis_context
    reporter = RedisRuntimeHealthReporter(
        storage_id="storage-1",
        external_stream_id="channel-01",
        redis_client=client,
        runtime_keys=runtime_keys,
        alert_keys=alert_keys,
        stream_max_length=5,
    )

    reporter.publish_failure("master_playlist_unavailable")

    _, fields = client.client.xrevrange(
        alert_keys.outbox(), count=1
    )[0]
    decoded = AlertEnvelope.from_redis_fields(fields)
    public_alert = parse_alert_fields(fields)
    assert decoded.category == AlertCategory.RUNTIME
    assert decoded.event_type == "RUNTIME_HEALTH"
    assert decoded.state == "DEGRADED"
    assert decoded.stream_id == "channel-01"
    assert decoded.event_id == runtime_health_event_id("storage-1")
    assert "storage-1" not in decoded.event_id
    assert public_alert.attributes == {
        "reasons": "master_playlist_unavailable"
    }

    reporter.publish(
        LiveCycleStats(
            started_at=datetime.now(timezone.utc),
            successful_snapshots=1,
        )
    )
    _, recovered_fields = client.client.xrevrange(
        alert_keys.outbox(), count=1
    )[0]
    recovered = AlertEnvelope.from_redis_fields(recovered_fields)
    recovered_public = parse_alert_fields(recovered_fields)
    assert recovered.state == "RECOVERED"
    assert recovered.reason == "runtime_recovered"
    assert recovered.stream_id == "channel-01"
    assert recovered_public.attributes == {"reasons": ""}

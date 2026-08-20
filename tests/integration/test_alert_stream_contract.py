from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest

from core.alert_stream import RedisAlertStream
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import RedisKeyBuilder
from core.runtime_health import RedisRuntimeHealthReporter
from models.alert import AlertCategory, AlertEnvelope
from models.runtime import LiveCycleStats


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
    keys = RedisKeyBuilder(
        prefix=f"media-monitor:test:{uuid4().hex}"
    )
    try:
        yield client, keys
    finally:
        found = list(
            client.client.scan_iter(match=f"{keys.prefix}:*")
        )
        if found:
            client.client.delete(*found)
        client.close()


def test_alert_stream_is_bounded_and_schema_is_consumable(redis_context):
    client, keys = redis_context
    stream = RedisAlertStream(key_builder=keys, max_length=5)
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
                stream_id="stream-1",
                occurred_at=now,
                emitted_at=now,
                reason="test",
            ),
        )
        pipeline.execute()

    assert client.client.xlen(keys.alert_outbox()) == 5
    _, latest = client.client.xrevrange(
        keys.alert_outbox(), count=1
    )[0]
    assert AlertEnvelope.from_redis_fields(latest).event_id == "event-11"
    metrics = client.client.hgetall(keys.runtime_metrics("stream-1"))
    assert metrics["alert_total"] == "12"
    assert metrics["alert_content_total"] == "12"


def test_runtime_health_uses_same_versioned_envelope(redis_context):
    client, keys = redis_context
    reporter = RedisRuntimeHealthReporter(
        stream_id="stream-1",
        redis_client=client,
        key_builder=keys,
        stream_max_length=5,
    )

    reporter.publish_failure("master_playlist_unavailable")

    _, fields = client.client.xrevrange(
        keys.alert_outbox(), count=1
    )[0]
    decoded = AlertEnvelope.from_redis_fields(fields)
    assert decoded.category == AlertCategory.RUNTIME
    assert decoded.event_type == "RUNTIME_HEALTH"
    assert decoded.state == "DEGRADED"
    assert decoded.event_id == "runtime-health:stream-1"

    reporter.publish(
        LiveCycleStats(
            started_at=datetime.now(timezone.utc),
            successful_snapshots=1,
        )
    )
    _, recovered_fields = client.client.xrevrange(
        keys.alert_outbox(), count=1
    )[0]
    recovered = AlertEnvelope.from_redis_fields(recovered_fields)
    assert recovered.state == "RECOVERED"
    assert recovered.reason == "runtime_recovered"

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest

from checks.video_freeze.live_state import RedisVideoFreezeEventStore
from checks.video_freeze.redis_keys import VideoFreezeRedisKeys
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from models.alert import AlertEnvelope
from models.freeze import FreezeInterval, VideoFreezeDetectionResult
from policies.video_freeze import VideoFreezeAlertPolicy
from tests.factories.hls import make_segment


pytestmark = pytest.mark.redis_integration


@dataclass(frozen=True)
class KeySpaces:
    namespace: RedisNamespace
    runtime: RuntimeRedisKeys
    alert: AlertRedisKeys
    freeze: VideoFreezeRedisKeys


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
    keys = KeySpaces(
        namespace=namespace,
        runtime=RuntimeRedisKeys(namespace),
        alert=AlertRedisKeys(namespace),
        freeze=VideoFreezeRedisKeys(namespace),
    )
    try:
        yield client, keys
    finally:
        found = list(client.client.scan_iter(match=f"{namespace.prefix}:*"))
        if found:
            client.client.delete(*found)
        client.close()


def store(client, keys, **kwargs):
    return RedisVideoFreezeEventStore(
        storage_id="internal-stream",
        external_stream_id="channel-01",
        redis_client=client,
        freeze_keys=keys.freeze,
        alert_keys=keys.alert,
        runtime_keys=keys.runtime,
        **kwargs,
    )


def detection(segment, *intervals):
    return VideoFreezeDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        freeze_intervals=list(intervals),
    )


def emitted(client, keys):
    return [
        AlertEnvelope.from_redis_fields(fields)
        for _entry_id, fields in client.client.xrange(keys.alert.outbox())
    ]


def segment(sequence, duration, *, variant="stable-720", at=None):
    item = make_segment(
        sequence,
        duration=duration,
        variant_id=variant,
        variant_stable_id=variant,
    )
    item.media_revision = f"revision-{sequence}"
    item.program_date_time = at
    return item


def test_warning_alert_upgrade_resolution_and_retry_are_idempotent(
    redis_context,
):
    client, keys = redis_context
    event_store = store(client, keys)
    first = segment(100, 3.0)
    second = segment(101, 2.0)
    returned = segment(102, 2.0)

    event_store.apply(
        segment=first,
        result=detection(first, FreezeInterval(0.0, 3.0)),
    )
    # Simulate a worker/session restart while the canonical event is open.
    event_store = store(client, keys)
    event_store.apply(
        segment=second,
        result=detection(second, FreezeInterval(0.0, 2.0)),
    )
    event_store.apply(segment=returned, result=detection(returned))
    event_store.apply(segment=returned, result=detection(returned))

    alerts = emitted(client, keys)
    assert [item.state for item in alerts] == [
        "OPEN",
        "UPDATE",
        "RESOLVED",
    ]
    assert [item.attributes["severity"] for item in alerts] == [
        "WARNING",
        "ALERT",
        "ALERT",
    ]
    assert len({item.event_id for item in alerts}) == 1
    assert len({item.alert_id for item in alerts}) == 3


def test_timeline_reset_and_variants_never_reuse_state(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    old = segment(100, 5.0, variant="stable-720")
    reset = segment(100, 5.0, variant="stable-720")
    reset.timeline_generation = 1
    other = segment(100, 5.0, variant="stable-1080")

    for item in (old, reset, other):
        event_store.apply(
            segment=item,
            result=detection(item, FreezeInterval(0.0, 5.0)),
        )

    alerts = emitted(client, keys)
    open_alerts = [item for item in alerts if item.state == "OPEN"]
    assert len(open_alerts) == 3
    assert len({item.event_id for item in open_alerts}) == 3
    assert keys.freeze.open_event(
        "internal-stream", "stable-1080"
    ) != keys.freeze.open_event("internal-stream", "stable-720")


def test_repeated_warning_window_is_bounded_and_emits_once(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sequence = 100
    for offset in (0, 20, 40):
        frozen = segment(sequence, 4.0, at=base + timedelta(seconds=offset))
        moving = segment(
            sequence + 1,
            2.0,
            at=base + timedelta(seconds=offset + 4),
        )
        event_store.apply(
            segment=frozen,
            result=detection(frozen, FreezeInterval(0.0, 4.0)),
        )
        event_store.apply(segment=moving, result=detection(moving))
        sequence += 2

    alerts = emitted(client, keys)
    repeated = [
        item for item in alerts if item.event_type == "REPEATED_VIDEO_FREEZE"
    ]
    assert len(repeated) == 1
    assert repeated[0].state == "OPEN"
    assert repeated[0].attributes["occurrences"] == "3"
    history_key = keys.freeze.short_history(
        "internal-stream", "stable-720", 0
    )
    assert client.client.zcard(history_key) == 3
    assert 0 < client.client.ttl(history_key) <= 240


def test_observation_gap_is_interrupted_not_recovered(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    frozen = segment(100, 3.0)
    gap = segment(105, 2.0)
    event_store.apply(
        segment=frozen,
        result=detection(frozen, FreezeInterval(0.0, 3.0)),
    )
    event_store.apply(segment=gap, result=detection(gap))

    alerts = emitted(client, keys)
    assert alerts[-1].state == "RESOLVED"
    assert alerts[-1].reason == "observation_gap"
    metrics = client.client.hgetall(
        keys.runtime.metrics("internal-stream")
    )
    assert metrics["video_freeze_interrupted_total"] == "1"
    assert "video_freeze_recovered_total" not in metrics


def test_high_event_rate_keeps_freeze_redis_state_bounded(redis_context):
    client, keys = redis_context
    event_store = store(
        client,
        keys,
        event_ttl_seconds=60,
        commit_ttl_seconds=30,
        alert_stream_max_length=20,
    )
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sequence = 1_000
    for index in range(40):
        event_at = base + timedelta(seconds=index * 5)
        frozen = segment(sequence, 4.0, at=event_at)
        moving = segment(
            sequence + 1,
            1.0,
            at=event_at + timedelta(seconds=4),
        )
        event_store.apply(
            segment=frozen,
            result=detection(frozen, FreezeInterval(0.0, 4.0)),
        )
        event_store.apply(segment=moving, result=detection(moving))
        sequence += 2

    history_key = keys.freeze.short_history(
        "internal-stream", "stable-720", 0
    )
    assert client.client.zcard(history_key) <= 25
    assert 0 < client.client.ttl(history_key) <= 240
    assert client.client.xlen(keys.alert.outbox()) <= 20

    detail_keys = list(
        client.client.scan_iter(
            match=(
                f"{keys.namespace.prefix}:freeze:internal-stream:"
                "variant:stable-720:event:*:details"
            )
        )
    )
    commit_keys = list(
        client.client.scan_iter(
            match=(
                f"{keys.namespace.prefix}:freeze:internal-stream:"
                "variant:stable-720:*:commit"
            )
        )
    )
    assert detail_keys
    assert commit_keys
    assert all(0 < client.client.ttl(key) <= 60 for key in detail_keys)
    assert all(0 < client.client.ttl(key) <= 30 for key in commit_keys)

from dataclasses import dataclass, replace
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
from core.frame_similarity import make_gray_fingerprint
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
    fingerprint = make_gray_fingerprint(bytes([100] * (32 * 32)))
    intervals = tuple(
        replace(
            item,
            start_boundary_fingerprint=(
                fingerprint if item.start <= 0.1 else None
            ),
            end_boundary_fingerprint=(
                fingerprint if item.end >= segment.duration - 0.1 else None
            ),
        )
        for item in intervals
    )
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


def test_continuous_open_resolution_and_retry_are_idempotent(
    redis_context,
):
    client, keys = redis_context
    event_store = store(client, keys)
    first = segment(100, 30.0)
    second = segment(101, 30.0)
    returned = segment(102, 2.0)

    event_store.apply(
        segment=first,
        result=detection(first, FreezeInterval(0.0, 30.0)),
    )
    # Simulate a worker/session restart while the canonical event is open.
    event_store = store(client, keys)
    event_store.apply(
        segment=second,
        result=detection(second, FreezeInterval(0.0, 30.0)),
    )
    event_store.apply(segment=returned, result=detection(returned))
    event_store.apply(segment=returned, result=detection(returned))

    alerts = emitted(client, keys)
    assert [item.state for item in alerts] == [
        "OPEN",
        "RESOLVED",
    ]
    assert [item.attributes["severity"] for item in alerts] == [
        "ALERT",
        "ALERT",
    ]
    assert len({item.event_id for item in alerts}) == 1
    assert len({item.alert_id for item in alerts}) == 2


def test_timeline_reset_and_variants_never_reuse_state(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    old = segment(100, 60.0, variant="stable-720")
    reset = segment(100, 60.0, variant="stable-720")
    reset.timeline_generation = 1
    other = segment(100, 60.0, variant="stable-1080")

    for item in (old, reset, other):
        event_store.apply(
            segment=item,
            result=detection(item, FreezeInterval(0.0, 60.0)),
        )

    alerts = emitted(client, keys)
    open_alerts = [item for item in alerts if item.state == "OPEN"]
    assert len(open_alerts) == 3
    assert len({item.event_id for item in open_alerts}) == 3
    assert keys.freeze.open_event(
        "internal-stream", "stable-1080"
    ) != keys.freeze.open_event("internal-stream", "stable-720")


def test_recovery_pending_survives_restart(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    frozen = segment(100, 60.0)
    partial_return = segment(101, 4.0)
    healthy = segment(102, 4.0)

    event_store.apply(
        segment=frozen,
        result=detection(frozen, FreezeInterval(0.0, 60.0)),
    )
    event_store.apply(
        segment=partial_return,
        result=detection(partial_return, FreezeInterval(0.0, 1.0)),
    )
    assert [item.state for item in emitted(client, keys)] == ["OPEN"]
    recovery_key = keys.freeze.alert_recovery(
        "internal-stream", "stable-720"
    )
    assert client.client.exists(recovery_key)

    event_store = store(client, keys)
    event_store.apply(segment=healthy, result=detection(healthy))

    alerts = emitted(client, keys)
    assert [item.state for item in alerts] == ["OPEN", "RESOLVED"]
    assert len({item.event_id for item in alerts}) == 1
    assert not client.client.exists(recovery_key)


def test_unknown_gap_does_not_confirm_pending_recovery(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    frozen = segment(100, 60.0)
    partial_return = segment(101, 4.0)
    unknown = segment(102, 4.0)
    healthy = segment(103, 4.0)

    event_store.apply(
        segment=frozen,
        result=detection(frozen, FreezeInterval(0.0, 60.0)),
    )
    event_store.apply(
        segment=partial_return,
        result=detection(partial_return, FreezeInterval(0.0, 1.0)),
    )
    failed = detection(unknown)
    failed.checked = False
    failed.error = "decode_failure"
    event_store.apply(segment=unknown, result=failed)
    assert [item.state for item in emitted(client, keys)] == ["OPEN"]

    event_store.apply(segment=healthy, result=detection(healthy))
    assert [item.state for item in emitted(client, keys)] == [
        "OPEN",
        "RESOLVED",
    ]


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
    assert [item.state for item in repeated] == ["OPEN", "RESOLVED"]
    assert len({item.event_id for item in repeated}) == 1
    assert repeated[0].attributes["occurrences"] == "3"
    history_key = keys.freeze.short_history(
        "internal-stream", "stable-720", 0
    )
    assert client.client.zcard(history_key) == 0


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
    assert alerts == []
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
                f"{keys.namespace.prefix}:stream:internal-stream:"
                "check:video_freeze:variant:stable-720:event:*:details"
            )
        )
    )
    commit_keys = list(
        client.client.scan_iter(
            match=(
                f"{keys.namespace.prefix}:stream:internal-stream:"
                "check:video_freeze:variant:stable-720:*:event-committed"
            )
        )
    )
    assert detail_keys
    assert commit_keys
    assert all(0 < client.client.ttl(key) <= 60 for key in detail_keys)
    assert all(0 < client.client.ttl(key) <= 30 for key in commit_keys)

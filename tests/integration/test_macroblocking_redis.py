from dataclasses import dataclass
import os
from uuid import uuid4

import pytest

from checks.macroblocking.live_state import (
    MacroblockingEventStateBusyError,
    RedisMacroblockingEventStore,
)
from checks.macroblocking.event_codec import MacroblockingEventCodec
from checks.macroblocking.redis_keys import MacroblockingRedisKeys
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from models.alert import AlertEnvelope
from models.macroblocking import MacroblockingDetectionResult, MacroblockingInterval
from tests.factories.hls import make_segment


pytestmark = pytest.mark.redis_integration


@dataclass(frozen=True)
class KeySpaces:
    namespace: RedisNamespace
    runtime: RuntimeRedisKeys
    alert: AlertRedisKeys
    macroblocking: MacroblockingRedisKeys


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
        macroblocking=MacroblockingRedisKeys(namespace),
    )
    try:
        yield client, keys
    finally:
        found = list(client.client.scan_iter(match=f"{namespace.prefix}:*"))
        if found:
            client.client.delete(*found)
        client.close()


def store(client, keys):
    return RedisMacroblockingEventStore(
        storage_id="storage-hash",
        external_stream_id="channel-01",
        redis_client=client,
        macroblocking_keys=keys.macroblocking,
        alert_keys=keys.alert,
        runtime_keys=keys.runtime,
    )


def segment(sequence, duration=5.0, *, timeline=0, variant="stable-1080"):
    item = make_segment(
        sequence,
        duration=duration,
        variant_id="1080p",
        variant_stable_id=variant,
    )
    item.timeline_generation = timeline
    item.media_revision = f"revision-{timeline}-{sequence}"
    return item


def interval(start, end, area=0.20):
    return MacroblockingInterval(
        start,
        end,
        area,
        max(area, 0.30),
        0.80,
        0.90,
        0.75,
    )


def detection(item, *intervals, checked=True, coverage_complete=True):
    return MacroblockingDetectionResult(
        variant_id=item.variant_id,
        sequence=item.sequence,
        segment_uri=item.uri,
        segment_duration=item.duration,
        intervals=intervals,
        checked=checked,
        coverage_complete=coverage_complete,
        error=None if checked else "decode_failure",
    )


def alerts(client, keys):
    return [
        AlertEnvelope.from_redis_fields(fields)
        for _entry_id, fields in client.client.xrange(keys.alert.outbox())
    ]


def test_open_restart_recovery_and_retry_are_exactly_once(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    first = segment(100)
    second = segment(101)
    healthy = segment(102)
    event_store.apply(segment=first, result=detection(first, interval(0, 5)))
    event_store.apply(segment=second, result=detection(second, interval(0, 5)))

    # Recreate the application boundary to prove Redis owns recovery state.
    event_store = store(client, keys)
    event_store.apply(segment=healthy, result=detection(healthy))
    event_store.apply(segment=healthy, result=detection(healthy))

    emitted = alerts(client, keys)
    assert [item.state for item in emitted] == ["OPEN", "RESOLVED"]
    assert len({item.event_id for item in emitted}) == 1
    assert len({item.alert_id for item in emitted}) == 2
    assert emitted[0].stream_id == "channel-01"
    assert emitted[0].attributes["start_segment_uri"].endswith("100.ts")
    assert emitted[0].attributes["end_segment_uri"].endswith("101.ts")


def test_unknown_gap_does_not_resolve_but_later_full_healthy_segment_does(
    redis_context,
):
    client, keys = redis_context
    event_store = store(client, keys)
    first = segment(100)
    second = segment(101)
    unknown = segment(102)
    healthy = segment(103)
    event_store.apply(segment=first, result=detection(first, interval(0, 5)))
    event_store.apply(segment=second, result=detection(second, interval(0, 5)))
    event_store.apply(
        segment=unknown,
        result=detection(unknown, checked=False, coverage_complete=False),
    )
    assert [item.state for item in alerts(client, keys)] == ["OPEN"]
    event_store.apply(segment=healthy, result=detection(healthy))
    assert [item.state for item in alerts(client, keys)] == ["OPEN", "RESOLVED"]


def test_timeline_reuse_and_variant_state_do_not_collide(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    for item in (
        segment(100, duration=10, timeline=0),
        segment(100, duration=10, timeline=1),
        segment(100, duration=10, timeline=0, variant="stable-720"),
    ):
        event_store.apply(
            segment=item,
            result=detection(item, interval(0, 10)),
        )
    emitted = [item for item in alerts(client, keys) if item.state == "OPEN"]
    # The unresolved alert from timeline 0 suppresses another OPEN for the
    # same variant, but the new canonical event must still have separate state.
    assert len(emitted) == 2
    assert len({item.event_id for item in emitted}) == 2
    current_raw = client.client.get(
        keys.macroblocking.open_event("storage-hash", "stable-1080")
    )
    current = MacroblockingEventCodec.decode(current_raw)
    assert current.timeline_generation == 1
    assert current.event_id != emitted[0].event_id
    assert client.client.exists(
        keys.macroblocking.event(
            "storage-hash", "stable-1080", emitted[0].event_id
        )
    )


def test_owned_lock_blocks_second_worker_without_partial_write(redis_context):
    client, keys = redis_context
    item = segment(100, duration=10)
    lock_key = keys.macroblocking.event_lock(
        "storage-hash", item.variant_stable_id
    )
    client.client.set(lock_key, "other-worker", px=30_000)
    with pytest.raises(MacroblockingEventStateBusyError):
        store(client, keys).apply(
            segment=item,
            result=detection(item, interval(0, 10)),
        )
    assert alerts(client, keys) == []


def test_payload_and_metrics_are_bounded_and_observable(redis_context):
    client, keys = redis_context
    item = segment(100, duration=10)
    store(client, keys).apply(
        segment=item,
        result=detection(item, interval(0, 10)),
    )
    raw = client.client.get(
        keys.macroblocking.open_event("storage-hash", item.variant_stable_id)
    )
    assert len(raw) < 2_000
    assert "observations" not in raw and "heatmap" not in raw
    metrics = client.client.hgetall(keys.runtime.metrics("storage-hash"))
    assert metrics["macroblocking_analysis_total"] == "1"
    assert metrics["macroblocking_candidate_interval_total"] == "1"
    assert metrics["macroblocking_alert_total"] == "1"

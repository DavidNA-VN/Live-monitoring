from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier
from uuid import uuid4

import pytest

from checks.black_screen.live_state import (
    BlackEventStateBusyError,
    RedisBlackEventStore,
)
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import (
    AlertRedisKeys,
    ProcessingRedisKeys,
    RedisNamespace,
    RuntimeRedisKeys,
)
from checks.black_screen.redis_keys import BlackScreenRedisKeys
from core.segment_state import RedisSegmentStateStore
from models.detection import BlackDetectionResult, BlackInterval
from policies.black_screen import BlackScreenAlertPolicy
from tests.factories.hls import make_segment


pytestmark = pytest.mark.redis_integration


@dataclass(frozen=True)
class RedisKeySpaces:
    namespace: RedisNamespace
    processing: ProcessingRedisKeys
    runtime: RuntimeRedisKeys
    alert: AlertRedisKeys
    black: BlackScreenRedisKeys


@pytest.fixture
def redis_context():
    url = os.getenv(
        "REDIS_TEST_URL",
        "redis://localhost:6379/15",
    )
    client = RedisClient(
        RedisSettings(
            url=url,
            socket_connect_timeout=0.25,
            socket_timeout=1.0,
        )
    )
    try:
        client.ping()
    except Exception as exc:
        client.close()
        pytest.skip(f"Disposable Redis is unavailable: {exc}")

    prefix = f"media-monitor:test:{uuid4().hex}"
    namespace = RedisNamespace(prefix)
    keys = RedisKeySpaces(
        namespace=namespace,
        processing=ProcessingRedisKeys(namespace),
        runtime=RuntimeRedisKeys(namespace),
        alert=AlertRedisKeys(namespace),
        black=BlackScreenRedisKeys(namespace),
    )
    try:
        yield client, keys
    finally:
        matching = list(
            client.client.scan_iter(match=f"{namespace.prefix}:*")
        )
        if matching:
            client.client.delete(*matching)
        client.close()


def result(segment, *intervals):
    return BlackDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        program_date_time=segment.program_date_time,
        black_intervals=list(intervals),
    )


def store(client, keys, *, policy=None):
    return RedisBlackEventStore(
        storage_id="stream-1",
        external_stream_id="channel-01",
        redis_client=client,
        black_keys=keys.black,
        alert_keys=keys.alert,
        runtime_keys=keys.runtime,
        policy=policy,
    )


def legacy_fast_policy():
    """Keep infrastructure tests fast without changing production defaults."""
    return BlackScreenAlertPolicy(direct_alert_duration=3.0)


def alerts(client, keys):
    return [
        fields
        for _, fields in client.client.xrange(
            keys.alert.outbox()
        )
    ]


def test_open_idempotency_and_restart_resolution(redis_context):
    client, keys = redis_context
    segment = make_segment(10)
    detection = result(
        segment,
        BlackInterval(start=0.0, end=6.0),
    )
    first_worker = store(client, keys, policy=legacy_fast_policy())

    first_worker.apply(segment=segment, result=detection)
    first_worker.apply(segment=segment, result=detection)

    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == ["OPEN"]

    restarted_worker = store(client, keys, policy=legacy_fast_policy())
    next_segment = make_segment(11)
    restarted_worker.apply(
        segment=next_segment,
        result=result(next_segment),
    )

    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == [
        "OPEN",
        "RESOLVED",
    ]


def test_timeline_generation_advance_is_atomic_and_persistent(redis_context):
    client, keys = redis_context
    state = RedisSegmentStateStore(
        redis_client=client,
        processing_keys=keys.processing,
    )

    def advance():
        return state.advance_timeline_generation(
            stream_id="stream-1",
            variant_stable_id="v720",
            expected_generation=0,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        generations = list(executor.map(lambda _item: advance(), range(8)))

    restarted = RedisSegmentStateStore(
        redis_client=client,
        processing_keys=keys.processing,
    )
    assert set(generations) == {1}
    assert restarted.get_timeline_generation(
        stream_id="stream-1",
        variant_stable_id="v720",
    ) == 1


def test_timeline_reset_reusing_sequence_creates_distinct_black_event(
    redis_context,
):
    client, keys = redis_context
    event_store = store(client, keys, policy=legacy_fast_policy())
    old = make_segment(100)
    old.media_revision = "same-manifest-media"
    current = make_segment(100)
    current.timeline_generation = 1
    current.media_revision = "same-manifest-media"

    event_store.apply(
        segment=old,
        result=result(old, BlackInterval(start=0.0, end=6.0)),
    )
    event_store.apply(
        segment=current,
        result=result(current, BlackInterval(start=0.0, end=6.0)),
    )

    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == [
        "OPEN",
        "RESOLVED",
        "OPEN",
    ]
    assert emitted[1]["reason"] == "timeline_discontinued"
    assert emitted[1]["event_id"] == emitted[0]["event_id"]
    assert emitted[0]["event_id"] != emitted[2]["event_id"]


def test_two_workers_do_not_emit_duplicate_open(redis_context):
    client, keys = redis_context
    segment = make_segment(10)
    detection = result(
        segment,
        BlackInterval(start=0.0, end=6.0),
    )
    workers = [
        store(client, keys, policy=legacy_fast_policy()),
        store(client, keys, policy=legacy_fast_policy()),
    ]
    barrier = Barrier(2)

    def apply(worker):
        barrier.wait()
        try:
            worker.apply(segment=segment, result=detection)
        except BlackEventStateBusyError:
            return "busy"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(apply, workers))

    for worker, outcome in zip(workers, outcomes):
        if outcome == "busy":
            worker.apply(segment=segment, result=detection)

    assert [
        item["state"] for item in alerts(client, keys)
    ] == ["OPEN"]


def test_canonical_events_are_stored_inside_variant_keyspace(redis_context):
    client, keys = redis_context
    event_store = store(client, keys, policy=legacy_fast_policy())
    first = make_segment(10, variant_id="720p")
    second = make_segment(
        10,
        variant_id="1080p",
        variant_stable_id="v1080",
    )

    event_store.apply(
        segment=first,
        result=result(first, BlackInterval(start=0.0, end=6.0)),
    )
    event_store.apply(
        segment=second,
        result=result(second, BlackInterval(start=0.0, end=6.0)),
    )

    emitted = alerts(client, keys)
    assert len(emitted) == 2
    for segment, alert in zip((first, second), emitted):
        event_key = keys.black.event(
            "stream-1",
            segment.variant_stable_id,
            alert["event_id"],
        )
        assert client.client.exists(event_key)

    assert not list(
        client.client.scan_iter(
            match=(
                f"{keys.namespace.prefix}:stream:stream-1:"
                "black:event:*"
            )
        )
    )


def test_preheld_event_lock_reports_retryable_contention(
    redis_context,
):
    client, keys = redis_context
    segment = make_segment(10)
    lock_key = keys.black.event_lock(
        stream_id="stream-1",
        variant_stable_id=segment.variant_stable_id,
    )
    client.client.set(lock_key, "other-worker", px=30_000)

    with pytest.raises(BlackEventStateBusyError):
        store(client, keys).apply(
            segment=segment,
            result=result(segment),
        )


def test_threshold_reached_on_resolution_emits_open_then_resolved(
    redis_context,
):
    client, keys = redis_context
    segment = make_segment(10)
    event_store = store(client, keys, policy=legacy_fast_policy())

    event_store.apply(
        segment=segment,
        result=result(
            segment,
            BlackInterval(start=1.0, end=4.5),
        ),
    )

    emitted = alerts(client, keys)
    # Closed event does NOT emit RESOLVED immediately; it enters recovery pending
    assert [item["state"] for item in emitted] == ["OPEN"]
    assert emitted[0]["reason"] == "threshold_reached_on_resolution"

    # Subsequent full healthy segment confirms recovery and emits RESOLVED
    healthy = make_segment(11)
    event_store.apply(segment=healthy, result=result(healthy))

    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == [
        "OPEN",
        "RESOLVED",
    ]
    assert emitted[1]["reason"] == "healthy_segment_confirmed"


def test_repeated_short_black_opens_and_recovers(redis_context):
    client, keys = redis_context
    policy = BlackScreenAlertPolicy(
        direct_alert_duration=60.0,
        repeated_event_count=3,
        repeated_window=120.0,
    )
    event_store = store(client, keys, policy=policy)
    started_at = datetime(2026, 8, 20, tzinfo=timezone.utc)

    for index in range(3):
        segment = make_segment(
            10 + (index * 2),
            duration=1.0,
            program_date_time=(
                started_at + timedelta(seconds=index * 10)
            ),
        )
        event_store.apply(
            segment=segment,
            result=result(
                segment,
                BlackInterval(start=0.0, end=1.0),
            ),
        )
        healthy = make_segment(
            segment.sequence + 1,
            duration=1.0,
            program_date_time=(
                segment.program_date_time + timedelta(seconds=1)
            ),
        )
        event_store.apply(
            segment=healthy,
            result=result(healthy),
        )

    repeated = [
        item
        for item in alerts(client, keys)
        if item["type"] == "REPEATED_BLACK_SCREEN"
    ]
    assert [item["state"] for item in repeated] == [
        "OPEN",
        "RESOLVED",
    ]
    assert repeated[0]["occurrences"] == "3"
    assert repeated[1]["event_id"] == repeated[0]["event_id"]

    fourth = make_segment(
        16,
        duration=1.0,
        program_date_time=started_at + timedelta(seconds=40),
    )
    event_store.apply(
        segment=fourth,
        result=result(fourth, BlackInterval(start=0.0, end=1.0)),
    )
    fourth_healthy = make_segment(
        17,
        duration=1.0,
        program_date_time=started_at + timedelta(seconds=41),
    )
    event_store.apply(
        segment=fourth_healthy,
        result=result(fourth_healthy),
    )
    assert len(
        [
            item
            for item in alerts(client, keys)
            if item["type"] == "REPEATED_BLACK_SCREEN"
        ]
    ) == 2


def test_sub_segment_black_is_not_counted_as_repeated_candidate(
    redis_context,
):
    client, keys = redis_context
    segment = make_segment(10, duration=6.0)

    store(client, keys).apply(
        segment=segment,
        result=result(segment, BlackInterval(start=1.0, end=2.0)),
    )

    history_key = keys.black.short_history(
        "stream-1", segment.variant_stable_id, segment.timeline_generation
    )
    assert client.client.zcard(history_key) == 0
    assert alerts(client, keys) == []


def test_observation_gap_never_emits_resolved_or_counts_repeated(
    redis_context,
):
    client, keys = redis_context
    policy = legacy_fast_policy()
    event_store = store(client, keys, policy=policy)
    opened = make_segment(10, duration=6.0)
    event_store.apply(
        segment=opened,
        result=result(opened, BlackInterval(start=0.0, end=6.0)),
    )

    after_gap = make_segment(12, duration=6.0)
    event_store.apply(segment=after_gap, result=result(after_gap))

    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == ["OPEN"]
    history_key = keys.black.short_history(
        "stream-1", opened.variant_stable_id, opened.timeline_generation
    )
    assert client.client.zcard(history_key) == 0
    assert not client.client.exists(
        keys.black.open_event("stream-1", opened.variant_stable_id)
    )


def test_continuous_open_at_60s_exactly_once(redis_context):
    client, keys = redis_context
    # Default policy: direct_alert_duration = 60.0
    event_store = store(client, keys)
    started_at = datetime(2026, 8, 20, tzinfo=timezone.utc)

    # 10 segments of 6.0s each = 60.0s total black
    for i in range(10):
        seg = make_segment(
            10 + i,
            duration=6.0,
            program_date_time=started_at + timedelta(seconds=i * 6),
        )
        event_store.apply(
            segment=seg,
            result=result(seg, BlackInterval(start=0.0, end=6.0)),
        )
        emitted = alerts(client, keys)
        if i < 9:  # 0 to 54s
            assert len(emitted) == 0
        else:  # reached 60.0s
            assert [item["state"] for item in emitted] == ["OPEN"]
            assert emitted[0]["type"] == "BLACK_SCREEN"

    # Next segment 11th (66s total black): should NOT emit duplicate OPEN
    seg_extra = make_segment(
        20,
        duration=6.0,
        program_date_time=started_at + timedelta(seconds=60),
    )
    event_store.apply(
        segment=seg_extra,
        result=result(seg_extra, BlackInterval(start=0.0, end=6.0)),
    )
    emitted = alerts(client, keys)
    assert len(emitted) == 1
    assert emitted[0]["state"] == "OPEN"


def test_interrupted_black_within_segment_does_not_emit_false_resolved(
    redis_context,
):
    client, keys = redis_context
    event_store = store(client, keys, policy=legacy_fast_policy())

    # Segment 10 has black ending at 5.0 (0.0 to 5.0, duration 5.0s >= 3.0s threshold -> OPEN)
    # Then normal video for 1.0s (5.0 to 6.0)
    seg10 = make_segment(10, duration=6.0)
    event_store.apply(
        segment=seg10,
        result=result(seg10, BlackInterval(start=0.0, end=5.0)),
    )
    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == ["OPEN"]

    # Segment 11 immediately has black again from start (start=0.0, end=4.0)
    # This is NOT a healthy segment; it must NOT confirm recovery or emit false RESOLVED
    seg11 = make_segment(11, duration=6.0)
    event_store.apply(
        segment=seg11,
        result=result(seg11, BlackInterval(start=0.0, end=4.0)),
    )

    emitted = alerts(client, keys)
    # State is still OPEN (or new OPEN for the second event), definitely NO RESOLVED
    assert all(item["state"] != "RESOLVED" for item in emitted)


def test_decode_error_does_not_trigger_false_recovery(redis_context):
    client, keys = redis_context
    event_store = store(client, keys, policy=legacy_fast_policy())

    seg10 = make_segment(10, duration=6.0)
    event_store.apply(
        segment=seg10,
        result=result(seg10, BlackInterval(start=0.0, end=6.0)),
    )
    assert [item["state"] for item in alerts(client, keys)] == ["OPEN"]

    # Segment 11 encounters decode failure / checked=False
    seg11 = make_segment(11, duration=6.0)
    event_store.apply(
        segment=seg11,
        result=BlackDetectionResult(
            variant_id=seg11.variant_id,
            sequence=seg11.sequence,
            segment_uri=seg11.uri,
            segment_duration=seg11.duration,
            checked=False,
            error="ffmpeg decode failed",
            black_intervals=[],
        ),
    )

    # UNKNOWN gap must close the unobserved media state but NEVER emit false RESOLVED
    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == ["OPEN"]


def test_deterministic_alert_id_and_outbox_contract(redis_context):
    client, keys = redis_context
    event_store = store(client, keys, policy=legacy_fast_policy())

    seg10 = make_segment(10, duration=6.0)
    event_store.apply(
        segment=seg10,
        result=result(seg10, BlackInterval(start=0.0, end=6.0)),
    )
    seg11 = make_segment(11, duration=6.0)
    event_store.apply(segment=seg11, result=result(seg11))

    emitted = alerts(client, keys)
    assert len(emitted) == 2
    open_alert, resolved_alert = emitted[0], emitted[1]
    assert open_alert["state"] == "OPEN"
    assert resolved_alert["state"] == "RESOLVED"
    # Both refer to the exact same continuous black event ID
    assert open_alert["event_id"] == resolved_alert["event_id"]
    assert open_alert["stream_id"] == "channel-01"
    assert resolved_alert["stream_id"] == "channel-01"
    assert open_alert["type"] == "BLACK_SCREEN"
    assert resolved_alert["type"] == "BLACK_SCREEN"
    assert len(open_alert["alert_id"]) == 36
    assert len(resolved_alert["alert_id"]) == 36


def test_black_return_preserves_original_open_alert_until_healthy(
    redis_context,
):
    client, keys = redis_context
    event_store = store(client, keys, policy=legacy_fast_policy())

    first = make_segment(10, duration=6.0)
    event_store.apply(
        segment=first,
        result=result(first, BlackInterval(start=0.0, end=5.0)),
    )
    original_open = alerts(client, keys)[0]

    returned = make_segment(11, duration=6.0)
    event_store.apply(
        segment=returned,
        result=result(returned, BlackInterval(start=0.0, end=6.0)),
    )
    assert [item["state"] for item in alerts(client, keys)] == ["OPEN"]

    healthy = make_segment(12, duration=6.0)
    event_store.apply(segment=healthy, result=result(healthy))

    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == ["OPEN", "RESOLVED"]
    assert emitted[1]["event_id"] == original_open["event_id"]


def test_unknown_gap_preserves_recovery_until_later_healthy_segment(
    redis_context,
):
    client, keys = redis_context
    event_store = store(client, keys, policy=legacy_fast_policy())
    opened = make_segment(10, duration=6.0)
    event_store.apply(
        segment=opened,
        result=result(opened, BlackInterval(start=0.0, end=6.0)),
    )

    unknown = make_segment(11, duration=6.0)
    event_store.apply(
        segment=unknown,
        result=BlackDetectionResult(
            variant_id=unknown.variant_id,
            sequence=unknown.sequence,
            segment_uri=unknown.uri,
            segment_duration=unknown.duration,
            checked=False,
            error="decode failed",
            black_intervals=[],
        ),
    )
    assert [item["state"] for item in alerts(client, keys)] == ["OPEN"]

    healthy = make_segment(12, duration=6.0)
    event_store.apply(segment=healthy, result=result(healthy))
    assert [item["state"] for item in alerts(client, keys)] == [
        "OPEN",
        "RESOLVED",
    ]


def test_timeline_change_closes_alert_without_claiming_healthy_recovery(
    redis_context,
):
    client, keys = redis_context
    event_store = store(client, keys, policy=legacy_fast_policy())
    opened = make_segment(10, duration=6.0)
    event_store.apply(
        segment=opened,
        result=result(opened, BlackInterval(start=0.0, end=5.0)),
    )

    reset = make_segment(10, duration=6.0)
    reset.timeline_generation = 1
    event_store.apply(segment=reset, result=result(reset))

    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == ["OPEN", "RESOLVED"]
    assert emitted[1]["reason"] == "timeline_discontinued"
    assert emitted[1]["event_id"] == emitted[0]["event_id"]

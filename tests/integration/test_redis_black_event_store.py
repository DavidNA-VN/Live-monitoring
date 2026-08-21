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
        stream_id="stream-1",
        redis_client=client,
        black_keys=keys.black,
        alert_keys=keys.alert,
        runtime_keys=keys.runtime,
        policy=policy,
    )


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
    first_worker = store(client, keys)

    first_worker.apply(segment=segment, result=detection)
    first_worker.apply(segment=segment, result=detection)

    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == ["OPEN"]

    restarted_worker = store(client, keys)
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
    event_store = store(client, keys)
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
    assert emitted[0]["event_id"] != emitted[2]["event_id"]
    assert emitted[1]["event_id"] == emitted[0]["event_id"]


def test_two_workers_do_not_emit_duplicate_open(redis_context):
    client, keys = redis_context
    segment = make_segment(10)
    detection = result(
        segment,
        BlackInterval(start=0.0, end=6.0),
    )
    workers = [store(client, keys), store(client, keys)]
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

    store(client, keys).apply(
        segment=segment,
        result=result(
            segment,
            BlackInterval(start=1.0, end=4.5),
        ),
    )

    emitted = alerts(client, keys)
    assert [item["state"] for item in emitted] == [
        "OPEN",
        "RESOLVED",
    ]
    assert emitted[0]["reason"] == (
        "threshold_reached_on_resolution"
    )
    assert emitted[1]["reason"] == "video_returned"


def test_repeated_short_black_opens_and_recovers(redis_context):
    client, keys = redis_context
    policy = BlackScreenAlertPolicy(
        direct_alert_duration=3.0,
        repeated_event_count=3,
        repeated_window=120.0,
        repeated_recovery_window=120.0,
    )
    event_store = store(client, keys, policy=policy)
    started_at = datetime(2026, 8, 20, tzinfo=timezone.utc)

    for index in range(6):
        segment = make_segment(
            10 + index,
            program_date_time=(
                started_at + timedelta(seconds=index * 10)
            ),
        )
        event_store.apply(
            segment=segment,
            result=result(
                segment,
                BlackInterval(start=1.0, end=2.0),
            ),
        )

    quiet_segment = make_segment(
        16,
        program_date_time=(
            started_at + timedelta(seconds=172)
        ),
    )
    event_store.apply(
        segment=quiet_segment,
        result=result(quiet_segment),
    )

    repeated = [
        item
        for item in alerts(client, keys)
        if item["type"] == "REPEATED_BLACK_SCREEN"
    ]
    assert [item["state"] for item in repeated] == [
        "OPEN",
        "UPDATE",
        "RESOLVED",
    ]
    assert repeated[0]["occurrences"] == "3"
    assert repeated[1]["occurrences"] == "6"
    assert repeated[2]["event_id"] == repeated[0]["event_id"]

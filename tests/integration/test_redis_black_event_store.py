from concurrent.futures import ThreadPoolExecutor
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
from core.redis_keys import RedisKeyBuilder
from models.detection import BlackDetectionResult, BlackInterval
from policies.black_screen import BlackScreenAlertPolicy
from tests.factories.hls import make_segment


pytestmark = pytest.mark.redis_integration


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
    keys = RedisKeyBuilder(prefix=prefix)
    try:
        yield client, keys
    finally:
        matching = list(
            client.client.scan_iter(match=f"{prefix}:*")
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
        key_builder=keys,
        policy=policy,
    )


def alerts(client, keys):
    return [
        fields
        for _, fields in client.client.xrange(
            keys.alert_outbox()
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
    lock_key = keys.black_event_lock(
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

from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest
import redis

from checks.video_freeze.live_state import RedisVideoFreezeEventStore
from checks.video_freeze.redis_keys import VideoFreezeRedisKeys
from core.alert_stream import AlertSink
from core.redis_client import RedisUnavailableError
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from models.freeze import FreezeInterval, VideoFreezeDetectionResult
from tests.factories.hls import make_segment


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.operations = []

    def _add(self, name, *args, **kwargs):
        self.operations.append((name, args, kwargs))
        return self

    def set(self, *args, **kwargs):
        return self._add("set", *args, **kwargs)

    def delete(self, *args):
        return self._add("delete", *args)

    def hincrby(self, *args):
        return self._add("hincrby", *args)

    def expire(self, *args):
        return self._add("expire", *args)

    def zadd(self, *args):
        return self._add("zadd", *args)

    def hset(self, *args, **kwargs):
        return self._add("hset", *args, **kwargs)

    def record_alert(self, envelope):
        return self._add("alert", envelope)

    def execute(self):
        if self.client.fail_next_transaction:
            self.client.fail_next_transaction = False
            raise redis.RedisError("transaction failed")
        for name, args, kwargs in self.operations:
            if name == "set":
                self.client.values[args[0]] = args[1]
            elif name == "delete":
                for key in args:
                    self.client.values.pop(key, None)
                    self.client.hashes.pop(key, None)
                    self.client.sorted_sets.pop(key, None)
            elif name == "hincrby":
                key, field, amount = args
                values = self.client.hashes.setdefault(key, {})
                values[field] = int(values.get(field, 0)) + amount
            elif name == "zadd":
                key, mapping = args
                self.client.sorted_sets.setdefault(key, {}).update(mapping)
            elif name == "hset":
                key = args[0]
                self.client.hashes.setdefault(key, {}).update(kwargs["mapping"])
            elif name == "alert":
                self.client.alerts.append(args[0])
        return [True] * len(self.operations)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.sorted_sets = {}
        self.alerts = []
        self.fail_next_transaction = False

    def exists(self, key):
        return key in self.values

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, *, nx=False, **_kwargs):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, _script, _count, key, token):
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0

    def pipeline(self, *, transaction):
        assert transaction is True
        return FakePipeline(self)

    def zrangebyscore(self, key, minimum, _maximum, *, withscores):
        assert withscores is True
        return sorted(
            (
                (event_id, score)
                for event_id, score in self.sorted_sets.get(key, {}).items()
                if score >= float(minimum)
            ),
            key=lambda item: item[1],
        )

    def hmget(self, key, fields):
        values = self.hashes.get(key, {})
        return [values.get(field) for field in fields]

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))


class TransactionalAlertSink(AlertSink):
    def append(self, pipeline, envelope):
        pipeline.record_alert(envelope)


@pytest.fixture
def context():
    namespace = RedisNamespace("monitor:freeze-test")
    client = FakeRedis()
    wrapper = SimpleNamespace(client=client)
    store = RedisVideoFreezeEventStore(
        storage_id="internal",
        external_stream_id="channel-01",
        redis_client=wrapper,
        freeze_keys=VideoFreezeRedisKeys(namespace),
        alert_keys=AlertRedisKeys(namespace),
        runtime_keys=RuntimeRedisKeys(namespace),
        alert_sink=TransactionalAlertSink(),
    )
    return store, client, VideoFreezeRedisKeys(namespace)


def detection(segment, *intervals):
    return VideoFreezeDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        freeze_intervals=list(intervals),
    )


def test_warning_upgrade_and_resolution_share_one_event(context):
    store, client, _keys = context
    first = make_segment(100, duration=3.0)
    second = make_segment(101, duration=2.0)
    moving = make_segment(102, duration=2.0)
    for item in (first, second, moving):
        item.media_revision = f"r-{item.sequence}"

    store.apply(
        segment=first,
        result=detection(first, FreezeInterval(0.0, 3.0)),
    )
    store.apply(
        segment=second,
        result=detection(second, FreezeInterval(0.0, 2.0)),
    )
    store.apply(segment=moving, result=detection(moving))

    assert [item.state for item in client.alerts] == [
        "OPEN",
        "UPDATE",
        "RESOLVED",
    ]
    assert len({item.event_id for item in client.alerts}) == 1


def test_freeze_below_warning_threshold_stays_internal(context):
    store, client, _keys = context
    segment = make_segment(10, duration=4.0)
    segment.media_revision = "r-10"

    store.apply(
        segment=segment,
        result=detection(segment, FreezeInterval(0.0, 2.9)),
    )

    assert client.alerts == []


def test_warning_reached_on_resolution_emits_atomic_open_resolved_pair(
    context,
):
    store, client, _keys = context
    segment = make_segment(10, duration=4.0)
    segment.media_revision = "r-10"

    store.apply(
        segment=segment,
        result=detection(segment, FreezeInterval(0.0, 3.2)),
    )

    assert [item.state for item in client.alerts] == ["OPEN", "RESOLVED"]
    assert [item.attributes["severity"] for item in client.alerts] == [
        "WARNING",
        "WARNING",
    ]
    assert len({item.event_id for item in client.alerts}) == 1


def test_failed_transaction_leaves_no_event_alert_or_commit(context):
    store, client, keys = context
    segment = make_segment(10, duration=3.0)
    segment.media_revision = "r-10"
    client.fail_next_transaction = True

    with pytest.raises(RedisUnavailableError, match="transaction failed"):
        store.apply(
            segment=segment,
            result=detection(segment, FreezeInterval(0.0, 3.0)),
        )

    commit = keys.commit_marker("internal", "v720", 0, 10, 0, "r-10")
    assert commit not in client.values
    assert keys.open_event("internal", "v720") not in client.values
    assert client.alerts == []


def test_three_warning_events_open_one_bounded_repeated_incident(context):
    store, client, keys = context
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(3):
        frozen = make_segment(index * 2, duration=4.0)
        moving = make_segment(index * 2 + 1, duration=2.0)
        frozen.program_date_time = base + timedelta(seconds=index * 10)
        moving.program_date_time = frozen.program_date_time + timedelta(
            seconds=4
        )
        frozen.media_revision = f"f-{index}"
        moving.media_revision = f"m-{index}"
        store.apply(
            segment=frozen,
            result=detection(frozen, FreezeInterval(0.0, 4.0)),
        )
        store.apply(segment=moving, result=detection(moving))

    repeated = [
        item
        for item in client.alerts
        if item.event_type == "REPEATED_VIDEO_FREEZE"
    ]
    history = keys.short_history("internal", "v720", 0)
    assert len(repeated) == 1
    assert repeated[0].attributes["occurrences"] == "3"
    assert len(client.sorted_sets[history]) == 3

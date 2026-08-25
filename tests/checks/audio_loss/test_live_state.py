from types import SimpleNamespace

import pytest
import redis

from checks.audio_loss.live_state import (
    AudioLossEventStateBusyError,
    RedisAudioLossEventStore,
)
from checks.audio_loss.redis_keys import AudioLossRedisKeys
from core.redis_client import RedisUnavailableError
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from models.audio import AudioTrackPresence, SilenceInterval
from models.audio_loss import AudioLossDetectionResult, AudioLossSignal
from policies.audio_loss import AudioLossAlertPolicy
from tests.factories.hls import make_segment


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.operations = []

    def set(self, key, value, **_options):
        self.operations.append(("set", key, value))
        return self

    def delete(self, key):
        self.operations.append(("delete", key))
        return self

    def record_alert(self, envelope):
        self.operations.append(("alert", envelope))

    def hincrby(self, key, field, value):
        self.operations.append(("hincrby", key, field, value))
        return self

    def expire(self, _key, _seconds):
        return self

    def execute(self):
        if self.client.fail_next_transaction:
            self.client.fail_next_transaction = False
            raise redis.RedisError("simulated transaction failure")
        for operation in self.operations:
            if operation[0] == "set":
                _, key, value = operation
                self.client.values[key] = value
            elif operation[0] == "delete":
                self.client.values.pop(operation[1], None)
            elif operation[0] == "hincrby":
                _, key, field, value = operation
                metrics = self.client.hashes.setdefault(key, {})
                metrics[field] = metrics.get(field, 0) + value
            else:
                self.client.alerts.append(operation[1])
        return [True] * len(self.operations)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.alerts = []
        self.hashes = {}
        self.fail_next_transaction = False

    def exists(self, key):
        return key in self.values

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, *, nx=False, **_options):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, _script, _key_count, key, token):
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0

    def pipeline(self, *, transaction):
        assert transaction is True
        return FakePipeline(self)


class TransactionalAlertSink:
    def append(self, pipeline, envelope):
        pipeline.record_alert(envelope)


@pytest.fixture
def context():
    namespace = RedisNamespace("monitor:test")
    client = FakeRedis()
    return (
        SimpleNamespace(client=client),
        client,
        AudioLossRedisKeys(namespace),
        AlertRedisKeys(namespace),
        RuntimeRedisKeys(namespace),
    )


def store(context, *, duration=30.0):
    wrapper, _client, audio_keys, alert_keys, runtime_keys = context
    return RedisAudioLossEventStore(
        stream_id="stream-1",
        redis_client=wrapper,
        policy=AudioLossAlertPolicy(alert_duration=duration),
        audio_keys=audio_keys,
        alert_keys=alert_keys,
        runtime_keys=runtime_keys,
        alert_sink=TransactionalAlertSink(),
    )


def result(segment, signal, *intervals):
    presence = (
        AudioTrackPresence.ABSENT
        if signal is AudioLossSignal.MISSING
        else AudioTrackPresence.PRESENT
    )
    return AudioLossDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        track_presence=presence,
        signal=signal,
        silence_intervals=list(intervals),
    )


def test_restart_recovery_and_segment_retry_are_idempotent(context):
    event_store = store(context)
    _wrapper, client, keys, _alert_keys, _runtime_keys = context

    for sequence in range(100, 115):
        segment = make_segment(sequence, duration=2.0)
        segment.media_revision = f"revision-{sequence}"
        event_store.apply(
            segment=segment,
            result=result(segment, AudioLossSignal.MISSING),
        )

    last = make_segment(114, duration=2.0)
    last.media_revision = "revision-114"
    event_store.apply(
        segment=last,
        result=result(last, AudioLossSignal.MISSING),
    )

    assert [alert.state for alert in client.alerts] == ["OPEN"]
    assert client.alerts[0].attributes["affected_segment_count"] == "15"

    restarted = store(context)
    recovered = make_segment(115, duration=2.0)
    recovered.media_revision = "revision-115"
    restarted.apply(
        segment=recovered,
        result=result(recovered, AudioLossSignal.AUDIBLE),
    )

    assert [alert.state for alert in client.alerts] == ["OPEN", "RESOLVED"]
    assert client.alerts[0].event_id == client.alerts[1].event_id
    assert keys.open_event("stream-1", "v720") not in client.values


def test_transaction_failure_writes_no_state_alert_or_commit(context):
    event_store = store(context, duration=2.0)
    _wrapper, client, keys, _alert_keys, _runtime_keys = context
    segment = make_segment(10, duration=2.0)
    segment.media_revision = "revision-10"
    detection = result(segment, AudioLossSignal.MISSING)
    client.fail_next_transaction = True

    with pytest.raises(RedisUnavailableError, match="simulated"):
        event_store.apply(segment=segment, result=detection)

    commit_key = keys.commit_marker(
        "stream-1", "v720", 0, 10, 0, "revision-10"
    )
    assert commit_key not in client.values
    assert keys.open_event("stream-1", "v720") not in client.values
    assert client.alerts == []

    event_store.apply(segment=segment, result=detection)
    assert commit_key in client.values
    assert [alert.state for alert in client.alerts] == ["OPEN"]


def test_two_variants_keep_independent_state_and_alert_identity(context):
    event_store = store(context)
    _wrapper, client, keys, _alert_keys, _runtime_keys = context
    first = make_segment(
        10,
        duration=30.0,
        variant_id="720p",
        variant_stable_id="stable-720",
    )
    second = make_segment(
        10,
        duration=30.0,
        variant_id="1080p",
        variant_stable_id="stable-1080",
    )

    for segment in (first, second):
        event_store.apply(
            segment=segment,
            result=result(segment, AudioLossSignal.MISSING),
        )

    assert keys.open_event("stream-1", "stable-720") in client.values
    assert keys.open_event("stream-1", "stable-1080") in client.values
    assert len(client.alerts) == 2
    assert client.alerts[0].event_id != client.alerts[1].event_id
    assert {
        alert.attributes["variant_stable_id"] for alert in client.alerts
    } == {"stable-720", "stable-1080"}


def test_threshold_reached_on_resolution_is_one_atomic_open_resolved_pair(
    context,
):
    event_store = store(context)
    _wrapper, client, _keys, _alert_keys, _runtime_keys = context
    segment = make_segment(10, duration=31.0)

    event_store.apply(
        segment=segment,
        result=result(
            segment,
            AudioLossSignal.SILENT,
            SilenceInterval(0.0, 30.0),
        ),
    )

    assert [alert.state for alert in client.alerts] == ["OPEN", "RESOLVED"]
    assert client.alerts[0].reason == "continuous_silence"
    assert client.alerts[1].reason == "audio_returned"
    assert client.alerts[0].event_id == client.alerts[1].event_id


def test_variant_lock_contention_is_retryable(context):
    event_store = store(context)
    _wrapper, client, keys, _alert_keys, _runtime_keys = context
    segment = make_segment(10)
    client.set(keys.event_lock("stream-1", "v720"), "other-worker")

    with pytest.raises(AudioLossEventStateBusyError):
        event_store.apply(
            segment=segment,
            result=result(segment, AudioLossSignal.AUDIBLE),
        )


@pytest.mark.parametrize(
    ("duration", "expected_states"),
    [
        (29.999, []),
        (30.0, ["OPEN"]),
        (30.001, ["OPEN"]),
    ],
)
def test_alert_boundary_is_enforced_by_event_store(
    context,
    duration,
    expected_states,
):
    event_store = store(context)
    _wrapper, client, _keys, _alert_keys, _runtime_keys = context
    segment = make_segment(10, duration=duration)

    event_store.apply(
        segment=segment,
        result=result(segment, AudioLossSignal.MISSING),
    )

    assert [alert.state for alert in client.alerts] == expected_states

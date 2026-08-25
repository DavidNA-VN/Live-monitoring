from dataclasses import dataclass
import json
import os
from uuid import uuid4

import pytest

from checks.audio_loss.event_codec import AudioLossEventCodec
from checks.audio_loss.live_state import RedisAudioLossEventStore
from checks.audio_loss.redis_keys import AudioLossRedisKeys
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from models.alert import AlertEnvelope
from models.audio import AudioTrackPresence
from models.audio_loss import AudioLossDetectionResult, AudioLossSignal
from tests.factories.hls import make_segment


pytestmark = pytest.mark.redis_integration


@dataclass(frozen=True)
class RedisKeySpaces:
    namespace: RedisNamespace
    runtime: RuntimeRedisKeys
    alert: AlertRedisKeys
    audio: AudioLossRedisKeys


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
    keys = RedisKeySpaces(
        namespace=namespace,
        runtime=RuntimeRedisKeys(namespace),
        alert=AlertRedisKeys(namespace),
        audio=AudioLossRedisKeys(namespace),
    )
    try:
        yield client, keys
    finally:
        found = list(client.client.scan_iter(match=f"{namespace.prefix}:*"))
        if found:
            client.client.delete(*found)
        client.close()


def store(client, keys):
    return RedisAudioLossEventStore(
        stream_id="stream-1",
        redis_client=client,
        audio_keys=keys.audio,
        alert_keys=keys.alert,
        runtime_keys=keys.runtime,
    )


def result(segment, signal):
    return AudioLossDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        track_presence=(
            AudioTrackPresence.ABSENT
            if signal is AudioLossSignal.MISSING
            else AudioTrackPresence.PRESENT
        ),
        signal=signal,
    )


def alerts(client, keys):
    return [
        AlertEnvelope.from_redis_fields(fields)
        for _, fields in client.client.xrange(keys.alert.outbox())
    ]


def test_restart_recovery_and_commit_idempotency(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    segments = []
    for sequence in range(100, 115):
        segment = make_segment(sequence, duration=2.0)
        segment.media_revision = f"revision-{sequence}"
        segments.append(segment)
        event_store.apply(
            segment=segment,
            result=result(segment, AudioLossSignal.MISSING),
        )

    event_store.apply(
        segment=segments[-1],
        result=result(segments[-1], AudioLossSignal.MISSING),
    )
    assert [item.state for item in alerts(client, keys)] == ["OPEN"]

    recovered = make_segment(115, duration=2.0)
    recovered.media_revision = "revision-115"
    store(client, keys).apply(
        segment=recovered,
        result=result(recovered, AudioLossSignal.AUDIBLE),
    )

    emitted = alerts(client, keys)
    assert [item.state for item in emitted] == ["OPEN", "RESOLVED"]
    assert emitted[0].event_id == emitted[1].event_id


def test_variants_do_not_overwrite_open_state_or_alerts(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    variants = [
        make_segment(
            10,
            duration=30.0,
            variant_id="720p",
            variant_stable_id="stable-720",
        ),
        make_segment(
            10,
            duration=30.0,
            variant_id="1080p",
            variant_stable_id="stable-1080",
        ),
    ]
    for segment in variants:
        event_store.apply(
            segment=segment,
            result=result(segment, AudioLossSignal.MISSING),
        )

    first_raw = client.client.get(
        keys.audio.open_event("stream-1", "stable-720")
    )
    second_raw = client.client.get(
        keys.audio.open_event("stream-1", "stable-1080")
    )
    assert first_raw and second_raw and first_raw != second_raw
    emitted = alerts(client, keys)
    assert len(emitted) == 2
    assert emitted[0].event_id != emitted[1].event_id
    assert {
        item.attributes["variant_stable_id"] for item in emitted
    } == {"stable-720", "stable-1080"}


def test_timeline_reset_reusing_sequence_creates_distinct_events(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    old = make_segment(100, duration=30.0)
    old.media_revision = "same-revision"
    current = make_segment(100, duration=30.0)
    current.timeline_generation = 1
    current.media_revision = "same-revision"

    event_store.apply(
        segment=old,
        result=result(old, AudioLossSignal.MISSING),
    )
    event_store.apply(
        segment=current,
        result=result(current, AudioLossSignal.MISSING),
    )

    emitted = alerts(client, keys)
    assert [item.state for item in emitted] == ["OPEN", "RESOLVED", "OPEN"]
    assert emitted[0].event_id == emitted[1].event_id
    assert emitted[0].event_id != emitted[2].event_id


def test_persisted_event_payload_remains_bounded(redis_context):
    client, keys = redis_context
    segment = make_segment(10, duration=30.0)
    store(client, keys).apply(
        segment=segment,
        result=result(segment, AudioLossSignal.MISSING),
    )
    emitted = alerts(client, keys)
    event_key = keys.audio.event(
        "stream-1",
        segment.variant_stable_id,
        emitted[0].event_id,
    )
    raw = client.client.get(event_key)
    payload = json.loads(raw)
    decoded = AudioLossEventCodec.decode(raw)

    assert "affected_segments" not in payload
    assert decoded.affected_segment_count == 1


def test_alert_transition_metrics_are_committed_with_outbox(redis_context):
    client, keys = redis_context
    event_store = store(client, keys)
    silent = make_segment(10, duration=30.0)
    audible = make_segment(11, duration=2.0)
    event_store.apply(
        segment=silent,
        result=result(silent, AudioLossSignal.MISSING),
    )
    event_store.apply(
        segment=audible,
        result=result(audible, AudioLossSignal.AUDIBLE),
    )

    metrics = client.client.hgetall(keys.runtime.metrics("stream-1"))

    assert metrics["audio_loss_open_total"] == "1"
    assert metrics["audio_loss_resolved_total"] == "1"

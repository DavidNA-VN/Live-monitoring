from datetime import datetime, timezone

from checks.audio_loss.alert_publisher import AudioLossAlertPublisher
from checks.audio_loss.event_reducer import AudioLossEventReducer
from checks.black_screen.alert_publisher import BlackAlertPublisher
from checks.black_screen.event_reducer import BlackEventReducer
from core.alert_stream import RedisAlertStream
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from core.runtime_health import (
    RedisRuntimeHealthReporter,
    runtime_health_event_id,
)
from models.alert import AlertCategory, AlertEnvelope, deterministic_alert_id
from models.audio_loss import AudioLossCause, AudioLossLiveEvent, AudioLossSignal
from models.black_live import BlackLiveEvent
from models.detection import BlackDetectionResult, BlackInterval
from models.runtime import LiveCycleStats
from tests.factories.hls import make_segment


class MemoryAlertSink:
    def __init__(self):
        self.envelopes: list[AlertEnvelope] = []

    def append(self, _pipeline, envelope: AlertEnvelope) -> None:
        self.envelopes.append(envelope)


class MemoryPipeline:
    def __init__(self):
        self.xadds = []
        self.increments = []

    def xadd(self, key, fields, **kwargs):
        self.xadds.append((key, fields))

    def hincrby(self, key, field, value):
        self.increments.append((key, field, value))

    def expire(self, key, seconds):
        pass


def test_alert_envelope_contract_isolates_storage_id():
    now = datetime.now(timezone.utc)
    envelope = AlertEnvelope(
        alert_id="alert-001",
        event_id="evt-001",
        category=AlertCategory.CONTENT,
        event_type="BLACK_SCREEN",
        state="OPEN",
        stream_id="channel-01",
        check="black_screen",
        variant_id="720p",
        variant_stable_id="v720",
        occurred_at=now,
        emitted_at=now,
        reason="continuous_black",
    )

    fields = envelope.to_redis_fields()
    assert "storage_id" not in fields
    assert fields["stream_id"] == "channel-01"
    assert fields["variant_id"] == "720p"
    assert fields["variant_stable_id"] == "v720"

    decoded = AlertEnvelope.from_redis_fields(fields)
    assert decoded.stream_id == "channel-01"
    assert decoded.variant_id == "720p"
    assert decoded.variant_stable_id == "v720"
    assert decoded == envelope


def test_black_alert_publisher_uses_storage_id_for_metrics_and_public_id_for_payload():
    namespace = RedisNamespace("test")
    pipeline = MemoryPipeline()
    publisher = BlackAlertPublisher(
        storage_id="storage-hash-123",
        external_stream_id="channel-01",
        alert_keys=AlertRedisKeys(namespace),
        runtime_keys=RuntimeRedisKeys(namespace),
    )

    now = datetime.now(timezone.utc)
    event = BlackLiveEvent(
        event_id="black-evt-123",
        stream_id="channel-01",
        variant_id="720p",
        variant_stable_id="v720",
        discontinuity_sequence=0,
        start_sequence=100,
        end_sequence=101,
        start_offset=0.0,
        end_offset=6.0,
        start_program_time=now,
        end_program_time=now,
        duration=6.0,
        last_segment_duration=6.0,
        affected_segments=[100, 101],
    )

    publisher.add_event(
        pipeline,
        event=event,
        state="OPEN",
        reason="continuous_black",
    )

    _outbox_key, fields = pipeline.xadds[0]
    assert fields["stream_id"] == "channel-01"
    assert fields["variant_id"] == "720p"
    assert fields["variant_stable_id"] == "v720"
    assert "storage_id" not in fields

    expected_alert_id = deterministic_alert_id(
        stream_id="storage-hash-123",
        event_id="black-evt-123",
        state="OPEN",
        reason="continuous_black",
        revision="101",
    )
    assert fields["alert_id"] == expected_alert_id

    metrics_keys = [item[0] for item in pipeline.increments]
    expected_metrics_key = RuntimeRedisKeys(namespace).metrics("storage-hash-123")
    assert all(k == expected_metrics_key for k in metrics_keys)


def test_audio_loss_alert_publisher_removes_duplicate_variant_stable_id_from_attributes():
    namespace = RedisNamespace("test")
    pipeline = MemoryPipeline()
    publisher = AudioLossAlertPublisher(
        storage_id="storage-hash-123",
        external_stream_id="channel-01",
        alert_keys=AlertRedisKeys(namespace),
        runtime_keys=RuntimeRedisKeys(namespace),
        threshold_dbfs=-60.0,
        threshold_duration=30.0,
    )

    now = datetime.now(timezone.utc)
    event = AudioLossLiveEvent(
        event_id="audio-evt-123",
        stream_id="channel-01",
        variant_id="720p",
        variant_stable_id="v720",
        discontinuity_sequence=0,
        start_sequence=100,
        end_sequence=101,
        start_offset=0.0,
        end_offset=2.0,
        start_program_time=now,
        end_program_time=now,
        duration=4.0,
        last_segment_duration=2.0,
        primary_cause=AudioLossCause.AUDIO_STREAM_MISSING,
        causes_seen=[AudioLossCause.AUDIO_STREAM_MISSING],
        affected_segment_count=2,
    )

    publisher.add_event(
        pipeline,
        event=event,
        state="OPEN",
        reason="audio_stream_missing",
    )

    _outbox_key, fields = pipeline.xadds[0]
    assert fields["stream_id"] == "channel-01"
    assert fields["variant_stable_id"] == "v720"
    assert "storage_id" not in fields

    decoded = AlertEnvelope.from_redis_fields(fields)
    assert "variant_stable_id" not in decoded.attributes
    assert decoded.variant_stable_id == "v720"


def test_reducers_produce_public_stream_id_and_storage_hashed_event_ids():
    black_reducer = BlackEventReducer(
        storage_id="storage-hash-123",
        external_stream_id="channel-01",
    )
    segment = make_segment(100, duration=6.0)
    transitions = black_reducer.reduce(
        open_event=None,
        segment=segment,
        result=BlackDetectionResult(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            program_date_time=None,
            black_intervals=[BlackInterval(0.0, 6.0)],
        ),
    )

    event = transitions[0].event
    assert event.stream_id == "channel-01"
    # Event ID is computed with storage_id
    expected_event_id = black_reducer._event_id(
        segment=segment,
        start_offset=0.0,
    )
    assert event.event_id == expected_event_id


def test_runtime_health_reporter_emits_storage_keys_and_external_stream_alert():
    class HealthRedis:
        def __init__(self):
            self.eval_args = []
            self.pipeline_instance = MemoryPipeline()

        def eval(self, *args):
            self.eval_args.append(args)
            return 0

        def pipeline(self, **kwargs):
            return self.pipeline_instance

    class Client:
        def __init__(self):
            self.client = HealthRedis()

    client = Client()
    reporter = RedisRuntimeHealthReporter(
        storage_id="storage-hash-123",
        external_stream_id="channel-01",
        redis_client=client,
    )

    reporter.publish_failure("master_playlist_unavailable")
    reporter.publish_failure("master_playlist_unavailable")

    call, retried_call = client.client.eval_args
    runtime_keys = RuntimeRedisKeys()
    assert call[2] == runtime_keys.health("storage-hash-123")
    assert call[4] == runtime_keys.metrics("storage-hash-123")
    assert call[8] == "channel-01"
    assert call[12] == runtime_health_event_id("storage-hash-123")
    assert "storage-hash-123" not in call[12]
    assert retried_call[11] == call[11]
    assert retried_call[12] == call[12]

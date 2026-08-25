from datetime import datetime, timezone

from checks.audio_loss.alert_publisher import AudioLossAlertPublisher
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from models.audio_loss import AudioLossCause, AudioLossLiveEvent


class CapturingSink:
    def __init__(self):
        self.envelopes = []

    def append(self, pipeline, envelope):
        self.envelopes.append(envelope)


class Pipeline:
    def __init__(self):
        self.increments = []

    def hincrby(self, key, field, value):
        self.increments.append((key, field, value))
        return self

    def expire(self, _key, _seconds):
        return self


def event():
    return AudioLossLiveEvent(
        event_id="event-1",
        stream_id="stream-1",
        variant_id="720p",
        variant_stable_id="variant-stable-720",
        discontinuity_sequence=0,
        start_sequence=100,
        end_sequence=114,
        start_offset=0.0,
        end_offset=2.0,
        start_program_time=datetime(2026, 8, 24, tzinfo=timezone.utc),
        end_program_time=datetime(2026, 8, 24, 0, 0, 30, tzinfo=timezone.utc),
        duration=30.0,
        last_segment_duration=2.0,
        primary_cause=AudioLossCause.AUDIO_STREAM_MISSING,
        causes_seen=[AudioLossCause.AUDIO_STREAM_MISSING],
        affected_segment_count=15,
        timeline_generation=2,
        start_media_revision="revision-a",
        last_media_revision="revision-b",
        audio_group="main",
        rendition_name="English",
        language="en",
        rendition_default=True,
        rendition_autoselect=True,
    )


def test_publisher_emits_canonical_variant_aware_contract():
    namespace = RedisNamespace("monitor:test")
    sink = CapturingSink()
    publisher = AudioLossAlertPublisher(
        stream_id="stream-1",
        alert_keys=AlertRedisKeys(namespace),
        runtime_keys=RuntimeRedisKeys(namespace),
        threshold_dbfs=-60.0,
        threshold_duration=30.0,
        alert_sink=sink,
    )
    audio_event = event()

    pipeline = Pipeline()
    publisher.add_event(
        pipeline,
        event=audio_event,
        state="OPEN",
        reason="audio_stream_missing",
    )
    publisher.add_event(
        pipeline,
        event=audio_event,
        state="RESOLVED",
        reason="audio_returned",
    )

    opened, resolved = sink.envelopes
    assert opened.event_type == "AUDIO_LOSS"
    assert opened.check == "audio_loss"
    assert opened.state == "OPEN"
    assert opened.reason == "audio_stream_missing"
    assert opened.attributes["variant_stable_id"] == "variant-stable-720"
    assert opened.attributes["threshold_dbfs"] == "-60"
    assert opened.attributes["threshold_duration"] == "30"
    assert opened.attributes["channel_mode"] == "all_channels"
    assert opened.attributes["audio_group"] == "main"
    assert opened.attributes["rendition_name"] == "English"
    assert opened.attributes["language"] == "en"
    assert opened.attributes["rendition_default"] == "true"
    assert resolved.state == "RESOLVED"
    assert resolved.reason == "audio_returned"
    assert resolved.alert_id != opened.alert_id
    assert [item[1] for item in pipeline.increments] == [
        "audio_loss_open_total",
        "audio_loss_resolved_total",
    ]


def test_publisher_alert_id_is_deterministic():
    namespace = RedisNamespace("monitor:test")
    sink = CapturingSink()
    publisher = AudioLossAlertPublisher(
        stream_id="stream-1",
        alert_keys=AlertRedisKeys(namespace),
        runtime_keys=RuntimeRedisKeys(namespace),
        threshold_dbfs=-60.0,
        threshold_duration=30.0,
        alert_sink=sink,
    )

    pipeline = Pipeline()
    publisher.add_event(
        pipeline,
        event=event(),
        state="OPEN",
        reason="audio_stream_missing",
    )
    publisher.add_event(
        pipeline,
        event=event(),
        state="OPEN",
        reason="audio_stream_missing",
    )

    assert sink.envelopes[0].alert_id == sink.envelopes[1].alert_id

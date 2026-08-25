import json
from datetime import datetime, timezone

from checks.audio_loss.event_codec import AudioLossEventCodec
from models.audio_loss import AudioLossCause, AudioLossLiveEvent


def event():
    return AudioLossLiveEvent(
        event_id="event-1",
        stream_id="stream-1",
        variant_id="720p",
        variant_stable_id="v720",
        discontinuity_sequence=3,
        start_sequence=100,
        end_sequence=114,
        start_offset=0.0,
        end_offset=2.0,
        start_program_time=datetime(2026, 8, 24, tzinfo=timezone.utc),
        end_program_time=datetime(2026, 8, 24, 0, 0, 30, tzinfo=timezone.utc),
        duration=30.0,
        last_segment_duration=2.0,
        primary_cause=AudioLossCause.AUDIO_STREAM_MISSING,
        causes_seen=[
            AudioLossCause.AUDIO_STREAM_MISSING,
            AudioLossCause.CONTINUOUS_SILENCE,
        ],
        affected_segment_count=15,
        alert_sent=True,
        timeline_generation=2,
        start_media_revision="revision-a",
        last_media_revision="revision-b",
        audio_group="main",
        rendition_name="English",
        language="en",
        rendition_default=True,
        rendition_autoselect=True,
    )


def test_codec_round_trip_preserves_bounded_event_state():
    original = event()

    decoded = AudioLossEventCodec.decode(AudioLossEventCodec.encode(original))

    assert decoded == original


def test_codec_does_not_persist_unbounded_segment_list():
    raw = AudioLossEventCodec.encode(event())
    payload = json.loads(raw)

    assert "affected_segments" not in payload
    assert payload["affected_segment_count"] == 15
    assert payload["start_sequence"] == 100
    assert payload["end_sequence"] == 114

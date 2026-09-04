from datetime import datetime, timezone

from checks.video_freeze.event_codec import VideoFreezeEventCodec
from checks.video_freeze.redis_keys import VideoFreezeRedisKeys
from core.redis_keys import RedisNamespace
from models.freeze import (
    VideoFreezeEventStatus,
    VideoFreezeLiveEvent,
    VideoFreezeSeverity,
)


def event() -> VideoFreezeLiveEvent:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return VideoFreezeLiveEvent(
        event_id="freeze-1",
        stream_id="channel-01",
        variant_id="720p",
        variant_stable_id="stable-720",
        timeline_generation=2,
        discontinuity_sequence=3,
        start_sequence=100,
        end_sequence=102,
        start_offset=0.0,
        end_offset=2.0,
        start_program_time=now,
        end_program_time=now,
        duration=6.0,
        last_segment_duration=2.0,
        start_media_revision="r100",
        last_media_revision="r102",
        affected_segment_count=3,
        status=VideoFreezeEventStatus.RESOLVED,
        highest_severity=VideoFreezeSeverity.ALERT,
        warning_sent=True,
        alert_sent=True,
        resolution_reason="video_returned",
    )


def test_event_codec_round_trip_preserves_timeline_and_notification_state():
    original = event()
    decoded = VideoFreezeEventCodec.decode(
        VideoFreezeEventCodec.encode(original)
    )

    assert decoded == original


def test_event_codec_reads_legacy_optional_fields_safely():
    original = event()
    payload = VideoFreezeEventCodec.encode(original)
    payload = payload.replace(',"highest_severity":"ALERT"', "")
    payload = payload.replace(',"timeline_generation":2', "")

    decoded = VideoFreezeEventCodec.decode(payload)

    assert decoded.timeline_generation == 0
    assert decoded.highest_severity is None


def test_keyspace_is_freeze_owned_and_full_identity_isolated():
    keys = VideoFreezeRedisKeys(RedisNamespace("monitor:test"))
    first = keys.commit_marker("internal", "v720", 0, 100, 1, "r1")
    replacement = keys.commit_marker(
        "internal", "v720", 0, 100, 1, "r2"
    )
    reset = keys.commit_marker("internal", "v720", 0, 100, 2, "r1")

    assert ":stream:internal:check:video_freeze:variant:v720:" in first
    assert len({first, replacement, reset}) == 3
    assert "channel-01" not in first

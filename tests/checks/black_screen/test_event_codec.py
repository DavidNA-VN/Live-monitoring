from datetime import datetime, timezone

from checks.black_screen.event_codec import BlackEventCodec
from models.black_live import BlackEventStatus, BlackLiveEvent


def test_event_codec_round_trip_preserves_domain_state():
    event = BlackLiveEvent(
        event_id="event-1",
        stream_id="stream-1",
        variant_id="720p",
        variant_stable_id="v720",
        discontinuity_sequence=2,
        start_sequence=10,
        end_sequence=12,
        start_offset=1.25,
        end_offset=4.5,
        start_program_time=datetime(
            2026,
            8,
            20,
            tzinfo=timezone.utc,
        ),
        end_program_time=datetime(
            2026,
            8,
            20,
            0,
            0,
            15,
            tzinfo=timezone.utc,
        ),
        duration=9.25,
        last_segment_duration=6.0,
        affected_segments=[10, 11, 12],
        status=BlackEventStatus.RESOLVED,
        long_alert_sent=True,
        resolution_reason="video_returned",
    )

    decoded = BlackEventCodec.decode(
        BlackEventCodec.encode(event)
    )

    assert decoded == event

import json
from datetime import datetime, timezone

import pytest

from checks.black_screen.event_codec import (
    BlackAlertRecoveryCodec,
    BlackEventCodec,
)
from models.black_live import (
    BlackAlertType,
    BlackAlertRecoveryState,
    BlackEventStatus,
    BlackLiveEvent,
)


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
            21,
            20,
            tzinfo=timezone.utc,
        ),
        end_program_time=datetime(
            2026,
            8,
            27,
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
        reference_segment_duration=4.0,
        detection_closed=True,
    )

    decoded = BlackEventCodec.decode(
        BlackEventCodec.encode(event)
    )

    assert decoded == event


def test_decode_legacy_json_without_new_fields_uses_safe_defaults():
    # Legacy event JSON lacking reference_segment_duration and detection_closed
    legacy_json = json.dumps(
        {
            "event_id": "legacy-1",
            "stream_id": "stream-1",
            "variant_id": "720p",
            "variant_stable_id": "v720",
            "discontinuity_sequence": 0,
            "start_sequence": 10,
            "end_sequence": 11,
            "start_offset": 0.0,
            "end_offset": 4.0,
            "start_program_time": None,
            "end_program_time": None,
            "duration": 4.0,
            "last_segment_duration": 4.0,
            "affected_segments": [10, 11],
            "status": "open",
            "long_alert_sent": False,
            "resolution_reason": None,
        }
    )

    decoded = BlackEventCodec.decode(legacy_json)
    # Infers reference_segment_duration from last_segment_duration
    assert decoded.reference_segment_duration == 4.0
    assert decoded.detection_closed is False
    assert decoded.status == BlackEventStatus.OPEN


def test_recovery_state_codec_roundtrip():
    state = BlackAlertRecoveryState(
        alert_event_id="alert-123",
        alert_type=BlackAlertType.CONTINUOUS,
        recovery_pending=True,
        healthy_segments_observed=1,
        last_observed_sequence=42,
        timeline_generation=0,
        discontinuity_sequence=2,
    )

    encoded = BlackAlertRecoveryCodec.encode(state)
    decoded = BlackAlertRecoveryCodec.decode(encoded)

    assert decoded == state


def test_event_codec_rejects_non_boolean_detection_closed():
    raw = BlackEventCodec.encode(
        BlackLiveEvent(
            event_id="event-1",
            stream_id="stream-1",
            variant_id="720p",
            variant_stable_id="v720",
            discontinuity_sequence=0,
            start_sequence=1,
            end_sequence=1,
            start_offset=0.0,
            end_offset=1.0,
            start_program_time=None,
            end_program_time=None,
            duration=1.0,
            last_segment_duration=4.0,
        )
    )
    payload = json.loads(raw)
    payload["detection_closed"] = "false"

    with pytest.raises(ValueError, match="detection_closed"):
        BlackEventCodec.decode(json.dumps(payload))


def test_recovery_codec_rejects_coerced_scalar_types():
    payload = {
        "alert_event_id": "alert-1",
        "alert_type": "continuous",
        "recovery_pending": "false",
    }
    with pytest.raises(ValueError, match="recovery_pending"):
        BlackAlertRecoveryCodec.decode(json.dumps(payload))

    payload["recovery_pending"] = False
    payload["healthy_segments_observed"] = "1"
    with pytest.raises(ValueError, match="healthy_segments_observed"):
        BlackAlertRecoveryCodec.decode(json.dumps(payload))

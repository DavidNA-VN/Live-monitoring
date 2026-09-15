import json

import pytest

from checks.macroblocking.event_codec import (
    MacroblockingAlertRecoveryCodec,
    MacroblockingEventCodec,
)
from models.macroblocking import (
    MacroblockingAlertRecoveryState,
    MacroblockingEventStatus,
    MacroblockingLiveEvent,
)


def event() -> MacroblockingLiveEvent:
    return MacroblockingLiveEvent(
        event_id="event-1",
        stream_id="channel-01",
        variant_id="1080p",
        variant_stable_id="high",
        timeline_generation=2,
        discontinuity_sequence=3,
        start_sequence=100,
        end_sequence=102,
        start_offset=0.0,
        end_offset=5.0,
        start_program_time=None,
        end_program_time=None,
        duration=15.0,
        evidence_duration=14.5,
        last_segment_duration=5.0,
        average_affected_area_ratio=0.25,
        peak_affected_area_ratio=0.40,
        average_blocking_confidence=0.80,
        peak_blocking_confidence=0.90,
        average_boundary_support_ratio=0.70,
        start_media_revision="r100",
        last_media_revision="r102",
        affected_segment_count=3,
        status=MacroblockingEventStatus.OPEN,
        alert_sent=True,
        start_segment_uri="https://cdn/100.ts",
        end_segment_uri="https://cdn/102.ts",
    )


def test_event_codec_round_trip_is_bounded_and_preserves_summary():
    raw = MacroblockingEventCodec.encode(event())
    restored = MacroblockingEventCodec.decode(raw)
    assert restored == event()
    assert "observations" not in raw
    assert "heatmap" not in raw
    assert len(raw) < 2_000


@pytest.mark.parametrize("field", ["alert_sent", "coverage_complete"])
def test_event_codec_rejects_non_boolean_fields(field):
    payload = json.loads(MacroblockingEventCodec.encode(event()))
    payload[field] = "true"
    with pytest.raises(ValueError, match="invalid macroblocking event payload"):
        MacroblockingEventCodec.decode(json.dumps(payload))


def test_recovery_codec_is_strict_and_round_trips():
    state = MacroblockingAlertRecoveryState(
        alert_event_id="event-1",
        recovery_pending=True,
        healthy_segments_observed=0,
        last_observed_sequence=102,
        timeline_generation=2,
        discontinuity_sequence=3,
    )
    assert MacroblockingAlertRecoveryCodec.decode(
        MacroblockingAlertRecoveryCodec.encode(state)
    ) == state
    payload = json.loads(MacroblockingAlertRecoveryCodec.encode(state))
    payload["healthy_segments_observed"] = True
    with pytest.raises(ValueError, match="invalid macroblocking recovery payload"):
        MacroblockingAlertRecoveryCodec.decode(json.dumps(payload))

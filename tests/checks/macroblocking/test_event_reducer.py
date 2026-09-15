from __future__ import annotations

import pytest

from checks.macroblocking.event_reducer import (
    MacroblockingEventReducer,
    MacroblockingEventTransitionType as TransitionType,
)
from models.macroblocking import MacroblockingDetectionResult, MacroblockingInterval
from models.segment import Segment


def _segment(sequence: int, duration: float = 5.0, **changes) -> Segment:
    values = {
        "variant_id": "1080p",
        "variant_stable_id": "variant-high",
        "sequence": sequence,
        "uri": f"https://cdn.example/{sequence}.ts",
        "duration": duration,
        "timeline_generation": 1,
        "discontinuity_sequence": 0,
        "media_revision": f"rev-{sequence}",
    }
    values.update(changes)
    return Segment(**values)


def _interval(start: float, end: float, area: float = 0.20) -> MacroblockingInterval:
    return MacroblockingInterval(
        start=start,
        end=end,
        average_affected_area_ratio=area,
        peak_affected_area_ratio=area,
        average_blocking_confidence=0.80,
        peak_blocking_confidence=0.90,
        average_boundary_support_ratio=0.75,
    )


def _result(
    segment: Segment,
    *intervals: MacroblockingInterval,
    checked: bool = True,
    coverage_complete: bool = True,
    error: str | None = None,
) -> MacroblockingDetectionResult:
    return MacroblockingDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        intervals=intervals,
        checked=checked,
        coverage_complete=coverage_complete,
        error=error,
    )


@pytest.fixture
def reducer() -> MacroblockingEventReducer:
    return MacroblockingEventReducer(
        storage_id="storage-hash",
        external_stream_id="channel-01",
    )


def _last_event(transitions):
    events = [item.event for item in transitions if item.event is not None]
    assert events
    return events[-1]


def test_ten_continuous_seconds_open_alert_exactly_once(reducer):
    first = _segment(100)
    first_result = reducer.reduce(
        open_event=None,
        segment=first,
        result=_result(first, _interval(0.0, 5.0)),
    )
    assert [item.type for item in first_result] == [TransitionType.PERSIST_OPEN]

    second = _segment(101)
    second_result = reducer.reduce(
        open_event=_last_event(first_result),
        segment=second,
        result=_result(second, _interval(0.0, 5.0)),
    )
    assert [item.type for item in second_result] == [TransitionType.OPEN_ALERT]
    event = _last_event(second_result)
    assert event.duration == pytest.approx(10.0)
    assert event.alert_sent is True
    assert event.start_segment_uri.endswith("100.ts")
    assert event.end_segment_uri.endswith("101.ts")

    third = _segment(102)
    third_result = reducer.reduce(
        open_event=event,
        segment=third,
        result=_result(third, _interval(0.0, 5.0)),
    )
    assert [item.type for item in third_result] == [TransitionType.PERSIST_OPEN]
    assert _last_event(third_result).duration == pytest.approx(15.0)


def test_nine_point_nine_seconds_does_not_alert(reducer):
    segment = _segment(100, duration=9.9)
    transitions = reducer.reduce(
        open_event=None,
        segment=segment,
        result=_result(segment, _interval(0.0, 9.9)),
    )
    assert [item.type for item in transitions] == [TransitionType.PERSIST_OPEN]
    assert _last_event(transitions).alert_sent is False


def test_ten_seconds_ending_inside_segment_opens_before_closing(reducer):
    segment = _segment(100, duration=12.0)
    transitions = reducer.reduce(
        open_event=None,
        segment=segment,
        result=_result(segment, _interval(0.0, 10.0)),
    )
    assert [item.type for item in transitions] == [
        TransitionType.OPEN_ALERT,
        TransitionType.CLOSE_EVENT,
    ]
    assert transitions[0].event.event_id == transitions[1].event.event_id
    assert transitions[0].event.alert_sent is True
    assert transitions[1].commits_segment is True


def test_duplicate_segment_does_not_double_duration_or_evidence(reducer):
    segment = _segment(100)
    first = reducer.reduce(
        open_event=None,
        segment=segment,
        result=_result(segment, _interval(0.0, 5.0)),
    )
    duplicate = reducer.reduce(
        open_event=_last_event(first),
        segment=segment,
        result=_result(segment, _interval(0.0, 5.0)),
    )
    event = _last_event(duplicate)
    assert event.duration == pytest.approx(5.0)
    assert event.evidence_duration == pytest.approx(5.0)
    assert event.affected_segment_count == 1


def test_short_negative_gap_is_tolerated_and_statistics_are_evidence_weighted(
    reducer,
):
    segment = _segment(100)
    transitions = reducer.reduce(
        open_event=None,
        segment=segment,
        result=_result(
            segment,
            _interval(0.0, 2.0, 0.20),
            _interval(2.4, 5.0, 0.40),
        ),
    )
    event = _last_event(transitions)
    assert event.duration == pytest.approx(5.0)
    assert event.evidence_duration == pytest.approx(4.6)
    assert event.average_affected_area_ratio == pytest.approx(
        (0.20 * 2.0 + 0.40 * 2.6) / 4.6
    )


def test_full_healthy_segment_closes_then_emits_recovery_evidence(reducer):
    affected = _segment(100, duration=10.0)
    opened = reducer.reduce(
        open_event=None,
        segment=affected,
        result=_result(affected, _interval(0.0, 10.0)),
    )
    healthy = _segment(101)
    transitions = reducer.reduce(
        open_event=_last_event(opened),
        segment=healthy,
        result=_result(healthy),
    )
    assert [item.type for item in transitions] == [
        TransitionType.CLOSE_EVENT,
        TransitionType.HEALTHY_EVIDENCE,
    ]
    assert transitions[0].event.event_id == _last_event(opened).event_id
    assert transitions[0].event.alert_sent is True
    assert transitions[0].reason == "video_returned"


@pytest.mark.parametrize(
    ("checked", "coverage_complete", "error"),
    [(False, False, "decode_failure"), (True, False, None)],
)
def test_unknown_observation_never_emits_healthy_evidence_or_false_resolution(
    reducer, checked, coverage_complete, error
):
    affected = _segment(100, duration=10.0)
    opened = reducer.reduce(
        open_event=None,
        segment=affected,
        result=_result(affected, _interval(0.0, 10.0)),
    )
    unknown = _segment(101)
    transitions = reducer.reduce(
        open_event=_last_event(opened),
        segment=unknown,
        result=_result(
            unknown,
            checked=checked,
            coverage_complete=coverage_complete,
            error=error,
        ),
    )
    assert [item.type for item in transitions] == [
        TransitionType.CLOSE_EVENT,
        TransitionType.UNKNOWN_GAP,
    ]
    assert all(item.type is not TransitionType.HEALTHY_EVIDENCE for item in transitions)


def test_sequence_gap_does_not_stitch_or_count_as_recovery(reducer):
    first = _segment(100)
    persisted = reducer.reduce(
        open_event=None,
        segment=first,
        result=_result(first, _interval(0.0, 5.0)),
    )
    after_gap = _segment(102)
    transitions = reducer.reduce(
        open_event=_last_event(persisted),
        segment=after_gap,
        result=_result(after_gap, _interval(0.0, 5.0)),
    )
    assert [item.type for item in transitions] == [
        TransitionType.CLOSE_EVENT,
        TransitionType.UNKNOWN_GAP,
        TransitionType.PERSIST_OPEN,
    ]
    assert _last_event(transitions).start_sequence == 102
    assert _last_event(transitions).duration == pytest.approx(5.0)


def test_event_identity_is_deterministic_and_public_stream_id_is_external(reducer):
    segment = _segment(100)
    first = reducer.reduce(
        open_event=None,
        segment=segment,
        result=_result(segment, _interval(0.0, 5.0)),
    )
    second = reducer.reduce(
        open_event=None,
        segment=segment,
        result=_result(segment, _interval(0.0, 5.0)),
    )
    assert _last_event(first).event_id == _last_event(second).event_id
    assert _last_event(first).stream_id == "channel-01"
    assert "storage-hash" not in _last_event(first).event_id

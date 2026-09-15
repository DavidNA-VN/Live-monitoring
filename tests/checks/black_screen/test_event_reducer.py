from datetime import datetime, timezone

import pytest

from checks.black_screen.event_reducer import (
    BlackEventReducer,
    BlackEventTransitionType,
)
from models.black_live import BlackLiveEvent
from models.detection import BlackDetectionResult, BlackInterval
from tests.factories.hls import make_segment


def detection(segment, *intervals, checked=True, error=None):
    return BlackDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        program_date_time=segment.program_date_time,
        checked=checked,
        error=error,
        black_intervals=list(intervals),
    )


def reduce(reducer, segment, *intervals, open_event=None, checked=True, error=None):
    return reducer.reduce(
        open_event=open_event,
        segment=segment,
        result=detection(segment, *intervals, checked=checked, error=error),
    )


def test_no_black_without_open_event_emits_healthy_evidence():
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )

    transitions = reduce(reducer, make_segment(10))

    assert len(transitions) == 1
    assert transitions[0].type == (
        BlackEventTransitionType.HEALTHY_EVIDENCE
    )
    assert transitions[0].event is None
    assert transitions[0].commits_segment is True


def test_short_interval_is_created_and_resolved_in_same_segment():
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    started_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    segment = make_segment(
        10,
        program_date_time=started_at,
    )

    transitions = reduce(
        reducer,
        segment,
        BlackInterval(start=1.0, end=2.5),
    )

    transition = transitions[0]
    event = transition.event
    assert transition.type == BlackEventTransitionType.CLOSE_EVENT
    assert transition.reason == "video_returned"
    assert transition.commits_segment is True
    assert event.detection_closed is True
    assert event.reference_segment_duration == segment.duration
    assert event.stream_id == "channel-01"
    assert event.start_sequence == 10
    assert event.end_sequence == 10
    assert event.duration == pytest.approx(1.5)
    assert event.start_program_time == started_at.replace(
        second=1
    )
    assert event.end_program_time == started_at.replace(
        second=2,
        microsecond=500_000,
    )


def test_black_reaching_segment_end_stays_open():
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    segment = make_segment(10, duration=6.0)

    transitions = reduce(
        reducer,
        segment,
        BlackInterval(start=2.0, end=5.95),
    )

    transition = transitions[0]
    assert transition.type == (
        BlackEventTransitionType.PERSIST_OPEN
    )
    assert transition.commits_segment is True
    assert transition.event.detection_closed is False
    assert transition.event.reference_segment_duration == 6.0
    assert transition.event.duration == pytest.approx(3.95)
    assert transition.event.stream_id == "channel-01"


def test_open_event_extends_across_adjacent_segment_boundary():
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    first = make_segment(10, duration=4.0)
    opened = reduce(
        reducer,
        first,
        BlackInterval(start=2.0, end=4.0),
    )[0].event
    assert opened.reference_segment_duration == 4.0

    second = make_segment(11, duration=6.0)

    transitions = reduce(
        reducer,
        second,
        BlackInterval(start=0.05, end=6.0),
        open_event=opened,
    )

    event = transitions[0].event
    assert transitions[0].type == (
        BlackEventTransitionType.PERSIST_OPEN
    )
    assert event.stream_id == "channel-01"
    assert event.start_sequence == 10
    assert event.end_sequence == 11
    assert event.duration == pytest.approx(8.0)
    assert event.affected_segments == [10, 11]
    assert event.start_segment_uri == first.uri
    assert event.end_segment_uri == second.uri
    assert event.coverage_complete is True
    # reference_segment_duration must be preserved from first segment
    assert event.reference_segment_duration == 4.0
    assert event.last_segment_duration == 6.0


def test_reducer_does_not_mutate_loaded_open_event():
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    first = make_segment(10)
    opened = reduce(
        reducer,
        first,
        BlackInterval(start=4.0, end=6.0),
    )[0].event
    original_duration = opened.duration
    original_segments = list(opened.affected_segments)

    reduce(
        reducer,
        make_segment(11),
        BlackInterval(start=0.0, end=6.0),
        open_event=opened,
    )

    assert opened.duration == original_duration
    assert opened.end_sequence == 10
    assert opened.affected_segments == original_segments


@pytest.mark.parametrize(
    ("segment", "reason"),
    [
        (make_segment(12), "observation_gap"),
        (
            make_segment(11, discontinuity_sequence=1),
            "observation_gap",
        ),
    ],
)
def test_gap_or_discontinuity_resolves_previous_event(
    segment,
    reason,
):
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    opened = reduce(
        reducer,
        make_segment(10),
        BlackInterval(start=4.0, end=6.0),
    )[0].event

    transitions = reduce(
        reducer,
        segment,
        open_event=opened,
    )

    assert [item.type for item in transitions] == [
        BlackEventTransitionType.UNKNOWN_GAP,
        BlackEventTransitionType.MARK_COMMITTED,
    ]
    assert transitions[0].reason == reason
    assert transitions[0].commits_segment is False
    assert transitions[1].commits_segment is True


def test_intervals_are_reduced_in_start_order():
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    segment = make_segment(10)

    transitions = reduce(
        reducer,
        segment,
        BlackInterval(start=4.0, end=5.0),
        BlackInterval(start=1.0, end=2.0),
    )

    assert [item.event.start_offset for item in transitions] == [
        1.0,
        4.0,
    ]
    assert transitions[0].commits_segment is False
    assert transitions[1].commits_segment is True


def test_same_segment_overlap_only_adds_new_black_duration():
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
    segment = make_segment(10)
    open_event = BlackLiveEvent(
        event_id="event-1",
        stream_id="storage-1",
        variant_id=segment.variant_id,
        variant_stable_id=segment.variant_stable_id,
        discontinuity_sequence=0,
        start_sequence=10,
        end_sequence=10,
        start_offset=1.0,
        end_offset=3.0,
        start_program_time=None,
        end_program_time=None,
        duration=2.0,
        last_segment_duration=6.0,
        affected_segments=[10],
        reference_segment_duration=6.0,
    )

    transition = reduce(
        reducer,
        segment,
        BlackInterval(start=2.0, end=4.0),
        open_event=open_event,
    )[0]

    assert transition.type == BlackEventTransitionType.CLOSE_EVENT
    assert transition.event.duration == pytest.approx(3.0)
    assert transition.event.stream_id == "channel-01"


def test_normal_gap_within_100ms_tolerance_is_merged():
    reducer = BlackEventReducer(
        storage_id="storage-1",
        external_stream_id="channel-01",
        boundary_tolerance=0.10,
    )
    segment = make_segment(10, duration=6.0)
    # Gap from 2.0 to 2.10 is exactly 100ms -> should merge
    transitions = reduce(
        reducer,
        segment,
        BlackInterval(start=1.0, end=2.0),
        BlackInterval(start=2.10, end=3.0),
    )

    assert len(transitions) == 1
    assert transitions[0].type == BlackEventTransitionType.CLOSE_EVENT
    assert transitions[0].event.duration == pytest.approx(2.0)


def test_normal_gap_500ms_splits_events():
    reducer = BlackEventReducer(
        storage_id="storage-1",
        external_stream_id="channel-01",
        boundary_tolerance=0.10,
    )
    segment = make_segment(10, duration=6.0)
    # Gap from 2.0 to 2.50 is 500ms -> > 100ms -> must split
    transitions = reduce(
        reducer,
        segment,
        BlackInterval(start=1.0, end=2.0),
        BlackInterval(start=2.50, end=4.0),
    )

    assert len(transitions) == 2
    # First event resolved at 2.0
    assert transitions[0].type == BlackEventTransitionType.CLOSE_EVENT
    assert transitions[0].event.start_offset == 1.0
    assert transitions[0].event.end_offset == 2.0
    assert transitions[0].event.detection_closed is True

    # Second event resolved at 4.0
    assert transitions[1].type == BlackEventTransitionType.CLOSE_EVENT
    assert transitions[1].event.start_offset == 2.50
    assert transitions[1].event.end_offset == 4.0
    assert transitions[1].event.event_id != transitions[0].event.event_id


def test_cross_segment_gap_500ms_splits_events():
    reducer = BlackEventReducer(
        storage_id="storage-1",
        external_stream_id="channel-01",
        boundary_tolerance=0.10,
    )
    first = make_segment(10, duration=6.0)
    # Ends at 6.0 (reach end)
    opened = reduce(
        reducer,
        first,
        BlackInterval(start=4.0, end=6.0),
    )[0].event

    second = make_segment(11, duration=6.0)
    # Starts at 0.50 (500ms normal gap after boundary) -> must NOT continue
    transitions = reduce(
        reducer,
        second,
        BlackInterval(start=0.50, end=3.0),
        open_event=opened,
    )

    # First transition: previous event resolves with black_interrupted
    assert transitions[0].type == BlackEventTransitionType.CLOSE_EVENT
    assert transitions[0].reason == "black_interrupted"
    assert transitions[0].event.end_sequence == 10
    assert transitions[0].event.detection_closed is True

    # Second transition: new event created in segment 11
    assert transitions[1].type == BlackEventTransitionType.CLOSE_EVENT
    assert transitions[1].event.start_sequence == 11
    assert transitions[1].event.event_id != opened.event_id


def test_decode_failure_emits_unknown_gap():
    reducer = BlackEventReducer(
        storage_id="storage-1",
        external_stream_id="channel-01",
    )
    segment = make_segment(10)
    transitions = reduce(
        reducer,
        segment,
        checked=False,
        error="FFmpeg decode failed",
    )

    assert len(transitions) == 1
    assert transitions[0].type == BlackEventTransitionType.UNKNOWN_GAP
    assert transitions[0].reason == "FFmpeg decode failed"
    assert transitions[0].commits_segment is True


def test_reducer_normalizes_legacy_open_event_stream_id():
    reducer = BlackEventReducer(
        storage_id="storage-1",
        external_stream_id="channel-01",
    )
    legacy_event = BlackLiveEvent(
        event_id="legacy-event-id",
        stream_id="legacy-hash-id",
        variant_id="720p",
        variant_stable_id="v720",
        discontinuity_sequence=0,
        start_sequence=10,
        end_sequence=10,
        start_offset=0.0,
        end_offset=6.0,
        start_program_time=None,
        end_program_time=None,
        duration=6.0,
        last_segment_duration=6.0,
        affected_segments=[10],
    )
    segment = make_segment(11, duration=6.0)
    transitions = reduce(
        reducer,
        segment,
        BlackInterval(start=0.0, end=6.0),
        open_event=legacy_event,
    )
    assert len(transitions) == 1
    assert transitions[0].event.stream_id == "channel-01"
    assert transitions[0].event.event_id == "legacy-event-id"


def test_negative_boundary_tolerance_is_rejected():
    with pytest.raises(
        ValueError,
        match="boundary_tolerance",
    ):
        BlackEventReducer(
            storage_id="storage-1",
            external_stream_id="channel-01",
            boundary_tolerance=-0.01,
        )

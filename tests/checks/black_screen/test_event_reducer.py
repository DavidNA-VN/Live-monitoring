from datetime import datetime, timezone

import pytest

from checks.black_screen.event_reducer import (
    BlackEventReducer,
    BlackEventTransitionType,
)
from models.black_live import BlackLiveEvent
from models.detection import BlackDetectionResult, BlackInterval
from tests.factories.hls import make_segment


def detection(segment, *intervals):
    return BlackDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        program_date_time=segment.program_date_time,
        black_intervals=list(intervals),
    )


def reduce(reducer, segment, *intervals, open_event=None):
    return reducer.reduce(
        open_event=open_event,
        segment=segment,
        result=detection(segment, *intervals),
    )


def test_no_black_without_open_event_only_marks_segment_committed():
    reducer = BlackEventReducer(stream_id="stream-1")

    transitions = reduce(reducer, make_segment(10))

    assert len(transitions) == 1
    assert transitions[0].type == (
        BlackEventTransitionType.MARK_COMMITTED
    )
    assert transitions[0].event is None
    assert transitions[0].commits_segment is True


def test_short_interval_is_created_and_resolved_in_same_segment():
    reducer = BlackEventReducer(stream_id="stream-1")
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
    assert transition.type == BlackEventTransitionType.RESOLVE
    assert transition.reason == "video_returned"
    assert transition.commits_segment is True
    assert event.stream_id == "stream-1"
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
    reducer = BlackEventReducer(stream_id="stream-1")
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
    assert transition.event.duration == pytest.approx(3.95)


def test_open_event_extends_across_adjacent_segment_boundary():
    reducer = BlackEventReducer(stream_id="stream-1")
    first = make_segment(10, duration=6.0)
    opened = reduce(
        reducer,
        first,
        BlackInterval(start=4.0, end=6.0),
    )[0].event
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
    assert event.start_sequence == 10
    assert event.end_sequence == 11
    assert event.duration == pytest.approx(7.95)
    assert event.affected_segments == [10, 11]


def test_reducer_does_not_mutate_loaded_open_event():
    reducer = BlackEventReducer(stream_id="stream-1")
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
    reducer = BlackEventReducer(stream_id="stream-1")
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
        BlackEventTransitionType.RESOLVE,
        BlackEventTransitionType.MARK_COMMITTED,
    ]
    assert transitions[0].reason == reason
    assert transitions[0].commits_segment is False
    assert transitions[1].commits_segment is True


def test_intervals_are_reduced_in_start_order():
    reducer = BlackEventReducer(stream_id="stream-1")
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
    reducer = BlackEventReducer(stream_id="stream-1")
    segment = make_segment(10)
    open_event = BlackLiveEvent(
        event_id="event-1",
        stream_id="stream-1",
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
    )

    transition = reduce(
        reducer,
        segment,
        BlackInterval(start=2.0, end=4.0),
        open_event=open_event,
    )[0]

    assert transition.type == BlackEventTransitionType.RESOLVE
    assert transition.event.duration == pytest.approx(3.0)


def test_negative_boundary_tolerance_is_rejected():
    with pytest.raises(
        ValueError,
        match="boundary_tolerance",
    ):
        BlackEventReducer(
            stream_id="stream-1",
            boundary_tolerance=-0.01,
        )

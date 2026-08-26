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
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )

    transitions = reduce(reducer, make_segment(10))

    assert len(transitions) == 1
    assert transitions[0].type == (
        BlackEventTransitionType.MARK_COMMITTED
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
    assert transition.type == BlackEventTransitionType.RESOLVE
    assert transition.reason == "video_returned"
    assert transition.commits_segment is True
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
    assert transition.event.duration == pytest.approx(3.95)
    assert transition.event.stream_id == "channel-01"


def test_open_event_extends_across_adjacent_segment_boundary():
    reducer = BlackEventReducer(
        storage_id="storage-1", external_stream_id="channel-01"
    )
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
    assert event.stream_id == "channel-01"
    assert event.start_sequence == 10
    assert event.end_sequence == 11
    assert event.duration == pytest.approx(7.95)
    assert event.affected_segments == [10, 11]


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
        BlackEventTransitionType.RESOLVE,
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
    )

    transition = reduce(
        reducer,
        segment,
        BlackInterval(start=2.0, end=4.0),
        open_event=open_event,
    )[0]

    assert transition.type == BlackEventTransitionType.RESOLVE
    assert transition.event.duration == pytest.approx(3.0)
    assert transition.event.stream_id == "channel-01"


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

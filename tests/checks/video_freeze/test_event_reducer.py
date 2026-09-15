from dataclasses import replace
from datetime import datetime, timezone

import pytest

from checks.video_freeze.event_reducer import (
    VideoFreezeEventReducer,
    VideoFreezeEventTransition,
    VideoFreezeEventTransitionType,
)
from models.freeze import (
    FreezeInterval,
    VideoFreezeDetectionResult,
    VideoFreezeEventStatus,
)
from core.frame_similarity import make_gray_fingerprint
from policies.video_freeze import VideoFreezeAlertPolicy, VideoFreezeSeverity
from tests.factories.hls import make_segment


def detection(segment, *intervals, checked=True, error=None):
    return VideoFreezeDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        program_date_time=segment.program_date_time,
        checked=checked,
        error=error,
        freeze_intervals=list(intervals),
    )


def reduce(reducer, segment, *intervals, open_event=None, checked=True):
    same_frame = make_gray_fingerprint(bytes([100] * (32 * 32)))
    intervals = tuple(
        replace(
            interval,
            start_boundary_fingerprint=(
                interval.start_boundary_fingerprint
                or (same_frame if interval.start <= 0.10 else None)
            ),
            end_boundary_fingerprint=(
                interval.end_boundary_fingerprint
                or (
                    same_frame
                    if interval.end >= segment.duration - 0.10
                    else None
                )
            ),
        )
        for interval in intervals
    )
    return reducer.reduce(
        open_event=open_event,
        segment=segment,
        result=detection(segment, *intervals, checked=checked),
    )


def reducer():
    return VideoFreezeEventReducer(
        storage_id="storage-1",
        external_stream_id="channel-01",
    )


def test_motion_without_open_event_marks_segment_committed():
    transition = reduce(reducer(), make_segment(10))[0]

    assert transition.type is VideoFreezeEventTransitionType.HEALTHY_EVIDENCE
    assert transition.event is None
    assert transition.commits_segment


def test_partial_freeze_resolves_when_video_returns_in_same_segment():
    started_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    segment = make_segment(10, duration=6.0, program_date_time=started_at)

    transition = reduce(
        reducer(),
        segment,
        FreezeInterval(1.25, 3.75),
    )[0]

    event = transition.event
    assert transition.type is VideoFreezeEventTransitionType.CLOSE_EVENT
    assert transition.reason == "video_returned"
    assert transition.commits_segment
    assert event.status is VideoFreezeEventStatus.RESOLVED
    assert event.resolution_reason == "video_returned"
    assert event.stream_id == "channel-01"
    assert event.duration == pytest.approx(2.5)
    assert event.start_program_time == started_at.replace(
        second=1,
        microsecond=250_000,
    )
    assert event.end_program_time == started_at.replace(
        second=3,
        microsecond=750_000,
    )


def test_freeze_reaching_segment_end_stays_open():
    transition = reduce(
        reducer(),
        make_segment(10, duration=2.0),
        FreezeInterval(1.2, 2.0),
    )[0]

    assert transition.type is VideoFreezeEventTransitionType.PERSIST_OPEN
    assert transition.commits_segment
    assert transition.event.duration == pytest.approx(0.8)
    assert transition.event.status is VideoFreezeEventStatus.OPEN


def test_freeze_across_three_segments_forms_one_event():
    instance = reducer()
    current = reduce(
        instance,
        make_segment(100, duration=2.0),
        FreezeInterval(1.2, 2.0),
    )[0].event
    event_id = current.event_id

    current = reduce(
        instance,
        make_segment(101, duration=2.0),
        FreezeInterval(0.0, 2.0),
        open_event=current,
    )[0].event
    transition = reduce(
        instance,
        make_segment(102, duration=2.0),
        FreezeInterval(0.0, 0.8),
        open_event=current,
    )[0]

    event = transition.event
    assert transition.type is VideoFreezeEventTransitionType.CLOSE_EVENT
    assert transition.reason == "video_returned"
    assert event.event_id == event_id
    assert event.start_sequence == 100
    assert event.end_sequence == 102
    assert event.start_offset == pytest.approx(1.2)
    assert event.end_offset == pytest.approx(0.8)
    assert event.duration == pytest.approx(3.6)
    assert event.affected_segment_count == 3
    assert event.start_segment_uri.endswith("/100.ts")
    assert event.end_segment_uri.endswith("/102.ts")
    assert event.coverage_complete is True


def test_reducer_does_not_mutate_loaded_event():
    instance = reducer()
    opened = reduce(
        instance,
        make_segment(10, duration=2.0),
        FreezeInterval(1.0, 2.0),
    )[0].event
    original = replace(opened)

    reduce(
        instance,
        make_segment(11, duration=2.0),
        FreezeInterval(0.0, 2.0),
        open_event=opened,
    )

    assert opened == original


@pytest.mark.parametrize(
    "changed_segment",
    [
        make_segment(12, duration=2.0),
        make_segment(11, duration=2.0, discontinuity_sequence=1),
        make_segment(11, duration=2.0, variant_stable_id="v1080"),
    ],
)
def test_identity_gap_resolves_old_event_and_starts_new_one(changed_segment):
    instance = reducer()
    opened = reduce(
        instance,
        make_segment(10, duration=2.0),
        FreezeInterval(1.0, 2.0),
    )[0].event

    transitions = reduce(
        instance,
        changed_segment,
        FreezeInterval(0.0, changed_segment.duration),
        open_event=opened,
    )

    assert [item.type for item in transitions] == [
        VideoFreezeEventTransitionType.UNKNOWN_GAP,
        VideoFreezeEventTransitionType.PERSIST_OPEN,
    ]
    assert transitions[0].reason in {
        "observation_gap",
        "timeline_discontinued",
    }
    assert transitions[0].event.status is VideoFreezeEventStatus.RESOLVED
    assert transitions[1].event.event_id != opened.event_id


@pytest.mark.parametrize(
    ("field", "old", "new"),
    [
        ("timeline_generation", 1, 2),
        ("media_revision", "revision-a", "revision-b"),
    ],
)
def test_timeline_reset_or_replacement_starts_new_event(field, old, new):
    instance = reducer()
    first = make_segment(10, duration=2.0)
    setattr(first, field, old)
    opened = reduce(
        instance,
        first,
        FreezeInterval(0.0, 2.0),
    )[0].event
    replacement = make_segment(10, duration=2.0)
    setattr(replacement, field, new)

    transitions = reduce(
        instance,
        replacement,
        FreezeInterval(0.0, 2.0),
        open_event=opened,
    )

    assert transitions[0].reason == (
        "timeline_discontinued"
        if field == "timeline_generation"
        else "observation_gap"
    )
    assert transitions[1].event.event_id != opened.event_id


def test_unknown_observation_emits_unknown_gap_without_mutating_loaded_event():
    instance = reducer()
    opened = reduce(
        instance,
        make_segment(10, duration=2.0),
        FreezeInterval(0.0, 2.0),
    )[0].event
    original = replace(opened)

    transitions = reduce(
        instance,
        make_segment(11, duration=2.0),
        open_event=opened,
        checked=False,
    )

    assert [item.type for item in transitions] == [
        VideoFreezeEventTransitionType.UNKNOWN_GAP,
        VideoFreezeEventTransitionType.UNKNOWN_GAP,
    ]
    assert opened == original


def test_unknown_then_later_freeze_closes_old_event_as_observation_gap():
    instance = reducer()
    opened = reduce(
        instance,
        make_segment(10, duration=2.0),
        FreezeInterval(0.0, 2.0),
    )[0].event

    unknown = reduce(
        instance,
        make_segment(11, duration=2.0),
        open_event=opened,
        checked=False,
    )
    assert unknown[-1].type is VideoFreezeEventTransitionType.UNKNOWN_GAP
    transitions = reduce(
        instance,
        make_segment(12, duration=2.0),
        FreezeInterval(0.0, 2.0),
        open_event=opened,
    )

    assert transitions[0].reason == "observation_gap"
    assert transitions[0].reason != "video_returned"
    assert transitions[1].event.event_id != opened.event_id


def test_same_segment_overlap_adds_only_new_duration():
    instance = reducer()
    segment = make_segment(10, duration=6.0)
    opened = reduce(
        instance,
        segment,
        FreezeInterval(1.0, 6.0),
    )[0].event

    transition = reduce(
        instance,
        segment,
        FreezeInterval(4.0, 6.0),
        open_event=opened,
    )[0]

    assert transition.event.duration == pytest.approx(5.0)
    assert transition.event.affected_segment_count == 1


def test_multiple_intervals_are_sorted_and_form_distinct_events():
    segment = make_segment(10, duration=6.0)

    transitions = reduce(
        reducer(),
        segment,
        FreezeInterval(4.0, 5.5),
        FreezeInterval(1.0, 2.0),
    )

    assert [item.type for item in transitions] == [
        VideoFreezeEventTransitionType.CLOSE_EVENT,
        VideoFreezeEventTransitionType.CLOSE_EVENT,
    ]
    assert [item.event.start_offset for item in transitions] == [1.0, 4.0]
    assert transitions[0].event.event_id != transitions[1].event.event_id
    assert not transitions[0].commits_segment
    assert transitions[1].commits_segment


def test_external_stream_id_is_normalized_without_changing_event_id():
    instance = reducer()
    opened = reduce(
        instance,
        make_segment(10, duration=2.0),
        FreezeInterval(0.0, 2.0),
    )[0].event
    opened.stream_id = "legacy-storage-hash"

    transition = reduce(
        instance,
        make_segment(11, duration=2.0),
        FreezeInterval(0.0, 2.0),
        open_event=opened,
    )[0]

    assert transition.event.stream_id == "channel-01"
    assert transition.event.event_id == opened.event_id


def test_continuous_threshold_uses_one_event_identity():
    instance = reducer()
    policy = VideoFreezeAlertPolicy()
    current = None
    event_ids = set()

    current = reduce(
        instance,
        make_segment(100, duration=30.0),
        FreezeInterval(0.0, 30.0),
    )[0].event
    event_ids.add(current.event_id)
    assert current.duration == pytest.approx(30.0)
    assert policy.pending_notification(
        duration=current.duration,
        warning_sent=current.warning_sent,
        alert_sent=current.alert_sent,
    ) is None

    current = reduce(
        instance,
        make_segment(101, duration=30.0),
        FreezeInterval(0.0, 30.0),
        open_event=current,
    )[0].event
    event_ids.add(current.event_id)

    assert current.duration == pytest.approx(60.0)
    assert policy.pending_notification(
        duration=current.duration,
        warning_sent=current.warning_sent,
        alert_sent=current.alert_sent,
    ) is VideoFreezeSeverity.ALERT
    assert len(event_ids) == 1


def test_different_boundary_frames_split_adjacent_freezes():
    instance = reducer()
    first_frame = make_gray_fingerprint(bytes([80] * (32 * 32)))
    second_frame = make_gray_fingerprint(bytes([180] * (32 * 32)))
    first_segment = make_segment(10, duration=2.0)
    opened = instance.reduce(
        open_event=None,
        segment=first_segment,
        result=detection(
            first_segment,
            FreezeInterval(
                0.0, 2.0, end_boundary_fingerprint=first_frame
            ),
        ),
    )[0].event
    second_segment = make_segment(11, duration=2.0)
    transitions = instance.reduce(
        open_event=opened,
        segment=second_segment,
        result=detection(
            second_segment,
            FreezeInterval(
                0.0,
                2.0,
                start_boundary_fingerprint=second_frame,
                end_boundary_fingerprint=second_frame,
            ),
        ),
    )
    assert transitions[0].type is VideoFreezeEventTransitionType.CLOSE_EVENT
    assert transitions[0].reason == "freeze_content_changed"
    assert transitions[1].event.event_id != opened.event_id


def test_missing_boundary_fingerprint_fails_closed():
    instance = reducer()
    first = make_segment(10, duration=2.0)
    opened = instance.reduce(
        open_event=None,
        segment=first,
        result=detection(first, FreezeInterval(0.0, 2.0)),
    )[0].event
    second = make_segment(11, duration=2.0)
    transitions = instance.reduce(
        open_event=opened,
        segment=second,
        result=detection(second, FreezeInterval(0.0, 2.0)),
    )
    assert transitions[0].reason == "freeze_content_changed"
    assert transitions[1].event.event_id != opened.event_id


@pytest.mark.parametrize("value", [-0.1, float("nan"), float("inf")])
def test_invalid_boundary_tolerance_is_rejected(value):
    with pytest.raises(ValueError, match="boundary_tolerance"):
        VideoFreezeEventReducer(
            storage_id="storage-1",
            external_stream_id="channel-01",
            boundary_tolerance=value,
        )


def test_transition_contract_requires_event_and_resolution_reason():
    with pytest.raises(ValueError, match="requires an event"):
        VideoFreezeEventTransition(
            type=VideoFreezeEventTransitionType.PERSIST_OPEN
        )
    with pytest.raises(ValueError, match="requires an event"):
        VideoFreezeEventTransition(
            type=VideoFreezeEventTransitionType.RESOLVE,
            reason="video_returned",
        )

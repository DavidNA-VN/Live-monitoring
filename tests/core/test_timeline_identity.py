from models.runtime import LiveCycleStats
from models.admission import LiveAdmissionPolicy, StartupAdmissionMode
from datetime import datetime, timezone

from checks.black_screen.event_reducer import BlackEventReducer
from core.live_polling import (
    InMemoryTimelineGenerationStore,
    PlaylistObservationTracker,
)
from core.redis_keys import ProcessingRedisKeys
from models.detection import BlackDetectionResult, BlackInterval
from models.processing import SegmentProcessingIdentity
from tests.factories.hls import make_segment, make_snapshot


def stats():
    return LiveCycleStats(started_at=datetime.now(timezone.utc))


def test_two_observers_advance_one_shared_generation_for_same_reset():
    store = InMemoryTimelineGenerationStore()
    first = PlaylistObservationTracker(
        stream_id="stream-1", generation_store=store
    )
    second = PlaylistObservationTracker(
        stream_id="stream-1", generation_store=store
    )
    previous = make_snapshot([500])
    reset = make_snapshot([100])
    first.observe(snapshot=previous, stats=stats())
    second.observe(snapshot=previous, stats=stats())

    first_result = first.observe(snapshot=reset, stats=stats())
    second_result = second.observe(snapshot=reset, stats=stats())

    assert first_result.timeline_generation == 1
    assert second_result.timeline_generation == 1


def test_observation_admits_full_first_snapshot_then_only_new_media():
    tracker = PlaylistObservationTracker(stream_id="stream-1")
    first = make_snapshot([100, 101, 102])
    second = make_snapshot([101, 102, 103])

    first_result = tracker.observe(snapshot=first, stats=stats())
    second_result = tracker.observe(snapshot=second, stats=stats())

    assert [item.sequence for item in first_result.admission_segments] == [
        100,
        101,
        102,
    ]
    assert [item.sequence for item in second_result.admission_segments] == [
        103
    ]


def test_bounded_startup_selects_newest_segments_and_records_scope():
    tracker = PlaylistObservationTracker(
        stream_id="stream-1",
        admission_policy=LiveAdmissionPolicy(
            startup_lookback_segments=4
        ),
    )
    cycle_stats = stats()

    result = tracker.observe(
        snapshot=make_snapshot([100, 101, 102, 103, 104, 105]),
        stats=cycle_stats,
    )

    assert [item.sequence for item in result.admission_segments] == [
        102,
        103,
        104,
        105,
    ]
    assert cycle_stats.startup_segments_selected == 4
    assert cycle_stats.startup_segments_outside_scope == 2


def test_full_snapshot_mode_preserves_audit_coverage():
    tracker = PlaylistObservationTracker(
        stream_id="stream-1",
        admission_policy=LiveAdmissionPolicy(
            startup_mode=StartupAdmissionMode.FULL_SNAPSHOT,
            startup_lookback_segments=1,
        ),
    )

    result = tracker.observe(
        snapshot=make_snapshot([100, 101, 102, 103, 104, 105]),
        stats=stats(),
    )

    assert [item.sequence for item in result.admission_segments] == [
        100,
        101,
        102,
        103,
        104,
        105,
    ]


def test_observation_admits_replacement_with_enriched_revision():
    tracker = PlaylistObservationTracker(stream_id="stream-1")
    first = make_snapshot(
        [100],
        segments=[make_segment(100, uri="https://media.test/a.ts")],
    )
    replacement = make_snapshot(
        [100],
        segments=[make_segment(100, uri="https://media.test/b.ts")],
    )

    first_result = tracker.observe(snapshot=first, stats=stats())
    replacement_result = tracker.observe(
        snapshot=replacement,
        stats=stats(),
    )

    assert [
        item.sequence for item in replacement_result.admission_segments
    ] == [100]
    assert (
        replacement_result.admission_segments[0].media_revision
        != first_result.admission_segments[0].media_revision
    )


def test_observation_admits_new_generation_after_timeline_reset():
    tracker = PlaylistObservationTracker(stream_id="stream-1")
    tracker.observe(snapshot=make_snapshot([500, 501]), stats=stats())

    result = tracker.observe(
        snapshot=make_snapshot([100, 101]),
        stats=stats(),
    )

    assert result.timeline_generation == 1
    assert [item.sequence for item in result.admission_segments] == [100, 101]
    assert {
        item.timeline_generation for item in result.admission_segments
    } == {1}


def test_timeline_reset_applies_bounded_startup_window():
    tracker = PlaylistObservationTracker(
        stream_id="stream-1",
        admission_policy=LiveAdmissionPolicy(
            startup_lookback_segments=3
        ),
    )
    tracker.observe(snapshot=make_snapshot([500, 501]), stats=stats())
    reset_stats = stats()

    result = tracker.observe(
        snapshot=make_snapshot([100, 101, 102, 103, 104]),
        stats=reset_stats,
    )

    assert [item.sequence for item in result.admission_segments] == [
        102,
        103,
        104,
    ]
    assert reset_stats.startup_segments_selected == 3
    assert reset_stats.startup_segments_outside_scope == 2


def test_redis_processing_key_separates_generation_and_revision():
    keys = ProcessingRedisKeys()
    base = dict(
        storage_id="stream-1",
        check_name="black_screen",
        variant_stable_id="v720",
        discontinuity_sequence=0,
        sequence=100,
    )
    old = SegmentProcessingIdentity(
        **base, timeline_generation=0, media_revision="old"
    )
    reset = SegmentProcessingIdentity(
        **base, timeline_generation=1, media_revision="old"
    )
    replacement = SegmentProcessingIdentity(
        **base, timeline_generation=1, media_revision="new"
    )

    assert len(
        {
            keys.segment_state(old),
            keys.segment_state(reset),
            keys.segment_state(replacement),
        }
    ) == 3


def test_black_event_id_separates_generation_and_media_revision():
    reducer = BlackEventReducer(
        storage_id="stream-1", external_stream_id="channel-01"
    )

    def event_id(generation, revision):
        segment = make_segment(100)
        segment.timeline_generation = generation
        segment.media_revision = revision
        result = BlackDetectionResult(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            program_date_time=None,
            black_intervals=[BlackInterval(0.0, 1.0)],
        )
        return reducer.reduce(
            open_event=None,
            segment=segment,
            result=result,
        )[0].event.event_id

    assert len(
        {
            event_id(0, "old"),
            event_id(1, "old"),
            event_id(1, "replacement"),
        }
    ) == 3

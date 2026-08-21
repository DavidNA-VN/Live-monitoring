from models.runtime import LiveCycleStats
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


def test_redis_processing_key_separates_generation_and_revision():
    keys = ProcessingRedisKeys()
    base = dict(
        stream_id="stream-1",
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
    reducer = BlackEventReducer(stream_id="stream-1")

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

import pytest

from checks.video_freeze.processor import VideoFreezeSegmentProcessor
from core.segment_processor import SegmentProcessOutcome
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
)
from models.freeze import FreezeInterval, VideoFreezeDetectionResult
from tests.factories.hls import make_segment


class FakeEventStore:
    def __init__(self):
        self.applied = []

    def apply(self, *, segment, result):
        self.applied.append((segment, result))


def bundle(*, checked=True, error=None, retryable=True, intervals=()):
    return SegmentAnalysisBundle(
        profile_name="video_realtime",
        video_realtime=VideoRealtimeAnalysis(
            checked=checked,
            error=error,
            retryable=retryable,
            outputs={
                AnalysisRequirement.FREEZE_INTERVALS: tuple(intervals)
            },
        ),
    )


def processor(event_store=None):
    return VideoFreezeSegmentProcessor(event_store or FakeEventStore())


def test_processor_declares_video_profile_requirement():
    instance = processor()

    assert instance.name == "video_freeze"
    assert instance.analysis_profile == "video_realtime"
    assert instance.requirements == {AnalysisRequirement.FREEZE_INTERVALS}


def test_processor_rejects_audio_only_segment():
    segment = make_segment(100)
    segment.has_video = False

    assert not processor().supports_segment(segment)


@pytest.mark.parametrize("retryable", [True, False])
def test_analysis_failure_follows_retry_contract(retryable):
    outcome = processor().process(
        make_segment(100),
        bundle(
            checked=False,
            error="video analysis failed",
            retryable=retryable,
        ),
    )

    assert not outcome.success
    assert outcome.retryable is retryable
    assert outcome.error == "video analysis failed"


def test_successful_motion_observation_is_committable_payload():
    outcome = processor().process(make_segment(100), bundle())

    assert outcome.success
    assert isinstance(outcome.payload, VideoFreezeDetectionResult)
    assert outcome.payload.checked
    assert outcome.payload.freeze_intervals == []


def test_successful_freeze_observation_is_payload():
    outcome = processor().process(
        make_segment(100),
        bundle(intervals=(FreezeInterval(1.0, 3.0),)),
    )

    assert outcome.success
    assert outcome.payload.freeze_intervals == [FreezeInterval(1.0, 3.0)]


def test_commit_applies_detection_result_to_event_store():
    segment = make_segment(100)
    result = VideoFreezeDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
    )
    store = FakeEventStore()

    VideoFreezeSegmentProcessor(store).commit(
        segment,
        SegmentProcessOutcome.ok(result),
    )

    assert store.applied == [(segment, result)]


def test_commit_rejects_invalid_payload():
    with pytest.raises(RuntimeError, match="without detection result"):
        processor().commit(
            make_segment(100),
            SegmentProcessOutcome.ok("invalid"),
        )

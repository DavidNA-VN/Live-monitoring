import pytest

from checks.black_screen.processor import (
    BlackScreenSegmentProcessor,
)
from core.segment_processor import SegmentProcessOutcome
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
)
from models.detection import BlackDetectionResult, BlackInterval
from tests.factories.hls import make_segment


class FakeEventStore:
    def __init__(self):
        self.applied = []

    def apply(self, *, segment, result):
        self.applied.append((segment, result))


def bundle(
    *,
    checked=True,
    error=None,
    retryable=True,
    intervals=(),
):
    return SegmentAnalysisBundle(
        profile_name="video_realtime",
        video_realtime=VideoRealtimeAnalysis(
            checked=checked,
            error=error,
            retryable=retryable,
            outputs={
                AnalysisRequirement.BLACK_INTERVALS: tuple(intervals)
            },
        ),
    )


def processor(event_store=None):
    return BlackScreenSegmentProcessor(
        event_store=event_store or FakeEventStore()
    )


def test_processor_declares_video_profile_requirement():
    instance = processor()

    assert instance.analysis_profile == "video_realtime"
    assert instance.requirements


def test_processor_rejects_audio_only_segment():
    segment = make_segment(100)
    segment.has_video = False

    assert processor().supports_segment(segment) is False


def test_profile_failure_is_retryable():
    outcome = processor().process(
        make_segment(100),
        bundle(
            checked=False,
            error="ffmpeg timeout",
        ),
    )

    assert outcome.success is False
    assert outcome.retryable is True
    assert outcome.error == "ffmpeg timeout"


def test_unsupported_media_is_terminal():
    outcome = processor().process(
        make_segment(100),
        bundle(
            checked=False,
            error="unsupported encryption",
            retryable=False,
        ),
    )

    assert outcome.success is False
    assert outcome.retryable is False
    assert outcome.error == "unsupported encryption"


def test_successful_no_black_result_still_commits_later():
    outcome = processor().process(
        make_segment(100),
        bundle(),
    )

    assert outcome.success is True
    assert outcome.payload.checked is True
    assert outcome.payload.black_intervals == []


def test_successful_black_result_is_payload():
    outcome = processor().process(
        make_segment(100),
        bundle(
            intervals=(
                BlackInterval(start=1.0, end=3.0),
            )
        ),
    )

    assert outcome.success is True
    assert outcome.payload.black_intervals[0].start == 1.0


def test_commit_applies_detection_result_to_event_store():
    segment = make_segment(100)
    result = BlackDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
        checked=True,
    )
    event_store = FakeEventStore()
    instance = processor(event_store)

    instance.commit(
        segment,
        SegmentProcessOutcome.ok(payload=result),
    )

    assert event_store.applied == [(segment, result)]


def test_commit_rejects_invalid_payload():
    with pytest.raises(RuntimeError):
        processor().commit(
            make_segment(100),
            SegmentProcessOutcome.ok(payload="wrong"),
        )

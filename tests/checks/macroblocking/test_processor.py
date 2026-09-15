from __future__ import annotations

import pytest

from checks.macroblocking.processor import MacroblockingSegmentProcessor
from core.segment_processor import SegmentProcessOutcome
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
)
from models.macroblocking import MacroblockingDetectionResult
from models.macroblocking import MacroblockingFrameObservation
from tests.factories.hls import make_segment


class RecordingStore:
    def __init__(self) -> None:
        self.calls = []

    def apply(self, *, segment, result) -> None:
        self.calls.append((segment, result))


def _analysis(*, checked=True, retryable=True):
    return SegmentAnalysisBundle(
        profile_name="video_realtime",
        video_realtime=VideoRealtimeAnalysis(
            checked=checked,
            retryable=retryable,
            error=None if checked else "analysis failed",
            outputs=(
                {AnalysisRequirement.MACROBLOCKING_OBSERVATIONS: ()}
                if checked
                else {}
            ),
        ),
    )


def test_processor_requests_only_shared_macroblocking_output():
    assert MacroblockingSegmentProcessor.analysis_profile == "video_realtime"
    assert MacroblockingSegmentProcessor.requirements == {
        AnalysisRequirement.MACROBLOCKING_OBSERVATIONS
    }


@pytest.mark.parametrize(
    ("retryable", "expected_retryable"),
    [(True, True), (False, False)],
)
def test_processor_maps_analysis_failure(retryable, expected_retryable):
    outcome = MacroblockingSegmentProcessor(RecordingStore()).process(
        make_segment(1),
        _analysis(checked=False, retryable=retryable),
    )
    assert outcome.success is False
    assert outcome.retryable is expected_retryable


def test_processor_commits_detection_result_to_port():
    segment = make_segment(2)
    store = RecordingStore()
    processor = MacroblockingSegmentProcessor(store)
    result = MacroblockingDetectionResult(
        variant_id=segment.variant_id,
        sequence=segment.sequence,
        segment_uri=segment.uri,
        segment_duration=segment.duration,
    )
    outcome = SegmentProcessOutcome.ok(result)

    processor.commit(segment, outcome)

    assert store.calls == [(segment, result)]


def test_partial_coverage_is_successful_unknown_payload_not_retry():
    segment = make_segment(4, duration=1.0)
    observation = MacroblockingFrameObservation(
        offset_seconds=0.0,
        affected_area_ratio=0.0,
        blocking_confidence=0.0,
        boundary_support_ratio=0.0,
        valid=False,
        invalid_reason="insufficient_luminance_variation",
    )
    analysis = SegmentAnalysisBundle(
        profile_name="video_realtime",
        video_realtime=VideoRealtimeAnalysis(
            checked=True,
            outputs={
                AnalysisRequirement.MACROBLOCKING_OBSERVATIONS: (observation,)
            },
        ),
    )

    outcome = MacroblockingSegmentProcessor(RecordingStore()).process(
        segment,
        analysis,
    )

    assert outcome.success is True
    assert outcome.retryable is False
    assert isinstance(outcome.payload, MacroblockingDetectionResult)
    assert outcome.payload.coverage_complete is False


def test_commit_rejects_missing_payload():
    with pytest.raises(RuntimeError, match="without detection result"):
        MacroblockingSegmentProcessor(RecordingStore()).commit(
            make_segment(3),
            SegmentProcessOutcome.ok(),
        )

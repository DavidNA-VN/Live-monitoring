import pytest

from detectors.video_freeze import VideoFreezeDetector
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
)
from models.freeze import FreezeInterval
from tests.factories.hls import make_segment


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


def test_detector_maps_video_analysis_to_freeze_result():
    segment = make_segment(100, duration=6.0)

    result = VideoFreezeDetector().detect(
        segment=segment,
        analysis=bundle(intervals=(FreezeInterval(1.0, 3.0),)),
    )

    assert result.checked
    assert result.sequence == segment.sequence
    assert result.variant_id == segment.variant_id
    assert result.segment_uri == segment.uri
    assert result.program_date_time == segment.program_date_time
    assert result.freeze_intervals == [FreezeInterval(1.0, 3.0)]
    assert result.has_freeze
    assert result.total_freeze_duration == pytest.approx(2.0)


def test_empty_intervals_are_valid_motion_observation():
    result = VideoFreezeDetector().detect(
        segment=make_segment(100),
        analysis=bundle(),
    )

    assert result.checked
    assert not result.has_freeze
    assert result.total_freeze_duration == 0.0


@pytest.mark.parametrize("retryable", [True, False])
def test_detector_preserves_analysis_failure_contract(retryable):
    result = VideoFreezeDetector().detect(
        segment=make_segment(100),
        analysis=bundle(
            checked=False,
            error="decode failed",
            retryable=retryable,
        ),
    )

    assert not result.checked
    assert result.retryable is retryable
    assert result.error == "decode failed"
    assert result.freeze_intervals == []


def test_detector_rejects_wrong_analysis_output_type():
    with pytest.raises(TypeError, match="freeze_intervals"):
        VideoFreezeDetector().detect(
            segment=make_segment(100),
            analysis=bundle(intervals=("not-an-interval",)),
        )


def test_detector_does_not_fall_back_when_requirement_is_missing():
    analysis = SegmentAnalysisBundle(
        profile_name="video_realtime",
        video_realtime=VideoRealtimeAnalysis(checked=True),
    )

    with pytest.raises(ValueError, match="freeze_intervals"):
        VideoFreezeDetector().detect(
            segment=make_segment(100),
            analysis=analysis,
        )

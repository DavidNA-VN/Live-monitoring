from detectors.black_screen import BlackScreenDetector
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
)
from models.detection import BlackInterval
from tests.factories.hls import make_segment


def test_detector_maps_video_analysis_to_black_result():
    segment = make_segment(100)
    analysis = SegmentAnalysisBundle(
        profile_name="video_realtime",
        video_realtime=VideoRealtimeAnalysis(
            checked=True,
            outputs={
                AnalysisRequirement.BLACK_INTERVALS: (
                    BlackInterval(start=1.0, end=3.0),
                )
            },
        ),
    )

    result = BlackScreenDetector().detect(
        segment=segment,
        analysis=analysis,
    )

    assert result.checked is True
    assert result.sequence == segment.sequence
    assert result.variant_id == segment.variant_id
    assert result.segment_uri == segment.uri
    assert result.black_intervals == [BlackInterval(start=1.0, end=3.0)]


def test_detector_maps_video_analysis_failure():
    segment = make_segment(100)
    analysis = SegmentAnalysisBundle(
        profile_name="video_realtime",
        video_realtime=VideoRealtimeAnalysis(
            checked=False,
            error="decode failed",
        ),
    )

    result = BlackScreenDetector().detect(
        segment=segment,
        analysis=analysis,
    )

    assert result.checked is False
    assert result.error == "decode failed"
    assert result.black_intervals == []

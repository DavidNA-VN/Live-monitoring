from __future__ import annotations

import pytest

from detectors.macroblocking_segment import MacroblockingDetector
from models.analysis import SegmentAnalysisBundle, VideoRealtimeAnalysis
from models.analysis import AnalysisRequirement
from models.macroblocking import MacroblockingFrameObservation
from tests.factories.hls import make_segment


def _observation(
    offset: float,
    *,
    area: float = 0.20,
    confidence: float = 0.80,
    support: float = 0.75,
    valid: bool = True,
) -> MacroblockingFrameObservation:
    return MacroblockingFrameObservation(
        offset_seconds=offset,
        affected_area_ratio=area,
        blocking_confidence=confidence,
        boundary_support_ratio=support,
        valid=valid,
        invalid_reason=None if valid else "decode_gap",
    )


def _bundle(
    observations=(),
    *,
    checked: bool = True,
    retryable: bool = True,
) -> SegmentAnalysisBundle:
    return SegmentAnalysisBundle(
        profile_name="video_realtime",
        video_realtime=VideoRealtimeAnalysis(
            checked=checked,
            retryable=retryable,
            error=None if checked else "ffmpeg failed",
            outputs=(
                {
                    AnalysisRequirement.MACROBLOCKING_OBSERVATIONS: tuple(
                        observations
                    )
                }
                if checked
                else {}
            ),
        ),
    )


def test_detector_aggregates_consecutive_candidates_with_weighted_statistics():
    detector = MacroblockingDetector(sampling_fps=1.0)
    segment = make_segment(20, duration=4.0, uri="https://cdn/20.ts")
    result = detector.detect(
        segment=segment,
        analysis=_bundle(
            (
                _observation(0.0, area=0.20, confidence=0.70, support=0.60),
                _observation(1.0, area=0.40, confidence=0.90, support=0.80),
                _observation(2.0, area=0.10),
                _observation(3.0, area=0.30, confidence=0.80, support=0.70),
            )
        ),
    )

    assert result.checked is True
    assert result.coverage_complete is True
    assert result.segment_uri == "https://cdn/20.ts"
    assert [(item.start, item.end) for item in result.intervals] == [
        (0.0, 2.0),
        (3.0, 4.0),
    ]
    first = result.intervals[0]
    assert first.average_affected_area_ratio == pytest.approx(0.30)
    assert first.peak_affected_area_ratio == pytest.approx(0.40)
    assert first.average_blocking_confidence == pytest.approx(0.80)
    assert first.average_boundary_support_ratio == pytest.approx(0.70)


def test_invalid_observation_breaks_interval_and_marks_partial_coverage():
    result = MacroblockingDetector(sampling_fps=1.0).detect(
        segment=make_segment(21, duration=3.0),
        analysis=_bundle(
            (
                _observation(0.0),
                _observation(1.0, valid=False, area=0.0, confidence=0.0),
                _observation(2.0),
            )
        ),
    )

    assert result.checked is True
    assert result.coverage_complete is False
    assert result.invalid_observation_count == 1
    assert [(item.start, item.end) for item in result.intervals] == [
        (0.0, 1.0),
        (2.0, 3.0),
    ]


def test_missing_samples_are_unknown_not_healthy():
    result = MacroblockingDetector(sampling_fps=1.0).detect(
        segment=make_segment(22, duration=6.0),
        analysis=_bundle((_observation(0.0, area=0.0, confidence=0.0),)),
    )
    assert result.checked is True
    assert result.coverage_complete is False
    assert result.intervals == ()


def test_missing_cadence_splits_positive_intervals():
    result = MacroblockingDetector(sampling_fps=1.0).detect(
        segment=make_segment(26, duration=4.0),
        analysis=_bundle((_observation(0.0), _observation(3.0))),
    )
    assert result.coverage_complete is False
    assert [(item.start, item.end) for item in result.intervals] == [
        (0.0, 1.0),
        (3.0, 4.0),
    ]


@pytest.mark.parametrize("retryable", [True, False])
def test_detector_preserves_profile_failure_retryability(retryable):
    result = MacroblockingDetector().detect(
        segment=make_segment(23),
        analysis=_bundle(checked=False, retryable=retryable),
    )
    assert result.checked is False
    assert result.retryable is retryable
    assert result.coverage_complete is False


def test_detector_rejects_unbounded_or_unordered_observations():
    detector = MacroblockingDetector(sampling_fps=1.0)
    segment = make_segment(24, duration=1.0)
    excessive = detector.detect(
        segment=segment,
        analysis=_bundle(tuple(_observation(index) for index in range(4))),
    )
    unordered = detector.detect(
        segment=make_segment(25, duration=3.0),
        analysis=_bundle((_observation(1.0), _observation(0.0))),
    )
    assert excessive.checked is False
    assert excessive.retryable is False
    assert unordered.checked is False
    assert unordered.retryable is False

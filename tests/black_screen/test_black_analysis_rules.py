from analyzers.transition import TransitionAnalyzer
from classifiers.black_screen import (
    BlackScreenClassifier,
    NEEDS_CONTEXT,
)
from events.black_event_aggregator import BlackScreenEvent
from models.black_analysis import (
    AudioEvidence,
    BitstreamEvidence,
    BitstreamSegmentCheck,
    CrossVariantEvidence,
)
from checks.black_screen.check import _events_overlap


def make_event(
    variant_id: str,
    start: float,
    end: float,
) -> BlackScreenEvent:
    return BlackScreenEvent(
        variant_id=variant_id,
        start_sequence=0,
        end_sequence=0,
        start_offset=0.0,
        end_offset=end - start,
        start_time=start,
        end_time=end,
        duration=end - start,
        affected_segments=[0],
    )


def test_transition_fade_requires_consistent_trend():
    analyzer = TransitionAnalyzer()

    noisy_decline = [
        150.0,
        170.0,
        130.0,
        160.0,
        110.0,
        120.0,
    ]
    real_fade = [
        180.0,
        165.0,
        148.0,
        130.0,
        105.0,
        80.0,
        55.0,
        30.0,
    ]

    assert not analyzer._is_decreasing(
        noisy_decline
    )
    assert analyzer._is_decreasing(real_fade)


def test_cross_variant_overlap_uses_ratio():
    long_event = make_event(
        "720p",
        5.0,
        15.0,
    )
    weak_overlap = make_event(
        "480p",
        14.0,
        20.0,
    )
    strong_overlap = make_event(
        "480p",
        6.0,
        14.0,
    )

    assert not _events_overlap(
        long_event,
        weak_overlap,
    )
    assert _events_overlap(
        long_event,
        strong_overlap,
    )


def test_classifier_confidence_is_not_technical_score():
    classifier = BlackScreenClassifier()
    event = make_event(
        "720p",
        0.0,
        6.0,
    )
    bitstream = BitstreamEvidence(
        checked_segment_count=1,
        failed_segment_count=0,
        has_bitstream_error=False,
    )
    audio = AudioEvidence(
        checked=True,
        has_audio=True,
        audio_active_during_black=False,
        silence_ratio=1.0,
    )
    cross_variant = CrossVariantEvidence(
        checked=True,
        analyzed_variant_count=2,
        overlapping_variant_ids=[
            "480p",
            "720p",
        ],
        min_overlap_ratio=1.0,
    )

    analysis = classifier.classify(
        event=event,
        bitstream=bitstream,
        audio=audio,
        cross_variant=cross_variant,
    )

    assert analysis.classification == NEEDS_CONTEXT
    assert analysis.technical_score != analysis.confidence


def test_bitstream_check_failed_is_not_bitstream_error():
    checks = [
        BitstreamSegmentCheck(
            sequence=0,
            uri="http://example.test/seg.ts",
            checked=False,
            has_error=False,
            returncode=-1,
            analyzer_error="ffmpeg timeout after 20s",
        )
    ]
    evidence = BitstreamEvidence(
        checked_segment_count=sum(
            1
            for check in checks
            if check.checked
        ),
        failed_segment_count=sum(
            1
            for check in checks
            if not check.checked
        ),
        has_bitstream_error=any(
            check.has_error
            for check in checks
        ),
        segment_checks=checks,
    )

    assert evidence.checked_segment_count == 0
    assert evidence.failed_segment_count == 1
    assert not evidence.has_bitstream_error

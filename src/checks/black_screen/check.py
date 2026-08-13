from dataclasses import dataclass, field

from analyzers.audio import AudioAnalyzer
from analyzers.bitstream import BitstreamAnalyzer
from analyzers.transition import TransitionAnalyzer
from classifiers.black_screen import BlackScreenClassifier
from classifiers.black_screen import (
    SUSPECTED_TECHNICAL_BLACK,
    TECHNICAL_ERROR,
)
from core.context import MonitoringContext
from core.monitor import CheckResult
from detectors.black_screen import BlackScreenDetector
from events.black_event_aggregator import (
    BlackEventAggregator,
    BlackScreenEvent,
)
from models.black_analysis import (
    AudioEvidence,
    BitstreamEvidence,
    BlackEventAnalysis,
    CrossVariantEvidence,
    TransitionEvidence,
)
from models.detection import BlackDetectionResult
from models.segment import Segment
from playlist.master_parser import Variant


@dataclass
class BlackScreenVariantResult:
    variant: Variant
    detection_results: list[BlackDetectionResult] = field(
        default_factory=list
    )
    events: list[BlackScreenEvent] = field(
        default_factory=list
    )
    event_analyses: list[BlackEventAnalysis] = field(
        default_factory=list
    )


@dataclass
class _LocalEventEvidence:
    event: BlackScreenEvent
    bitstream: BitstreamEvidence
    audio: AudioEvidence
    transition: TransitionEvidence


def _segments_for_event(
    event: BlackScreenEvent,
    segments: list[Segment],
) -> list[Segment]:

    affected_sequences = set(
        event.affected_segments
    )

    return [
        segment
        for segment in segments
        if segment.sequence in affected_sequences
    ]


def _events_overlap(
    first: BlackScreenEvent,
    second: BlackScreenEvent,
    min_overlap_ratio: float = 0.70,
) -> bool:

    return (
        _event_overlap_ratio(
            first,
            second,
        )
        >= min_overlap_ratio
    )


def _event_overlap_ratio(
    first: BlackScreenEvent,
    second: BlackScreenEvent,
) -> float:

    intersection = max(
        0.0,
        min(first.end_time, second.end_time)
        - max(first.start_time, second.start_time),
    )

    shortest_duration = min(
        first.duration,
        second.duration,
    )

    if shortest_duration <= 0.0:
        return 0.0

    return intersection / shortest_duration


def _cross_variant_evidence(
    event: BlackScreenEvent,
    all_events: list[BlackScreenEvent],
    analyzed_variant_count: int,
    min_overlap_ratio: float = 0.70,
) -> CrossVariantEvidence:

    overlapping_variant_ids = set()
    overlap_ratios = []

    for other in all_events:
        overlap_ratio = _event_overlap_ratio(
            event,
            other,
        )

        if overlap_ratio >= min_overlap_ratio:
            overlapping_variant_ids.add(
                other.variant_id
            )
            overlap_ratios.append(
                overlap_ratio
            )

    return CrossVariantEvidence(
        checked=True,
        analyzed_variant_count=analyzed_variant_count,
        overlapping_variant_ids=sorted(
            overlapping_variant_ids
        ),
        min_overlap_ratio=(
            min(overlap_ratios)
            if overlap_ratios
            else 0.0
        ),
    )


def _classify_events(
    results: dict[str, BlackScreenVariantResult],
    local_evidence: dict[str, list[_LocalEventEvidence]],
    classifier: BlackScreenClassifier,
) -> None:

    all_events = [
        event
        for variant_result in results.values()
        for event in variant_result.events
    ]

    analyzed_variant_count = len(results)

    for variant_result in results.values():
        analyses = []

        for evidence in local_evidence[
            variant_result.variant.id
        ]:
            cross_variant = _cross_variant_evidence(
                event=evidence.event,
                all_events=all_events,
                analyzed_variant_count=analyzed_variant_count,
            )

            analyses.append(
                classifier.classify(
                    event=evidence.event,
                    bitstream=evidence.bitstream,
                    audio=evidence.audio,
                    transition=evidence.transition,
                    cross_variant=cross_variant,
                )
            )

        variant_result.event_analyses = analyses


class BlackScreenCheck:
    name = "black_screen"

    def __init__(
        self,
        pix_th: float = 0.10,
        pic_th: float = 0.98,
    ):
        self.pix_th = pix_th
        self.pic_th = pic_th

    def run(
        self,
        context: MonitoringContext,
    ) -> CheckResult:

        results = self.run_raw(
            context
        )

        return CheckResult(
            name=self.name,
            result=results,
            has_issue=self._has_technical_issue(
                results
            ),
        )

    def run_raw(
        self,
        context: MonitoringContext,
    ) -> dict[str, BlackScreenVariantResult]:

        detector = BlackScreenDetector(
            pix_th=self.pix_th,
            pic_th=self.pic_th,
        )

        aggregator = BlackEventAggregator()
        bitstream_analyzer = BitstreamAnalyzer()
        audio_analyzer = AudioAnalyzer()
        transition_analyzer = TransitionAnalyzer()
        classifier = BlackScreenClassifier()

        results: dict[str, BlackScreenVariantResult] = {}
        local_evidence: dict[
            str,
            list[_LocalEventEvidence],
        ] = {}

        for variant in context.variants:
            segments = context.segments_for_variant(
                variant
            )

            detection_results = [
                detector.detect(segment)
                for segment in segments
            ]

            events = aggregator.aggregate(
                detection_results
            )

            local_evidence[variant.id] = []

            for event in events:
                affected_segments = _segments_for_event(
                    event=event,
                    segments=segments,
                )

                bitstream = (
                    bitstream_analyzer.analyze_segments(
                        affected_segments
                    )
                )
                audio = audio_analyzer.analyze_event(
                    variant=variant,
                    event=event,
                )
                transition = (
                    transition_analyzer.analyze_event(
                        variant=variant,
                        event=event,
                    )
                )

                local_evidence[variant.id].append(
                    _LocalEventEvidence(
                        event=event,
                        bitstream=bitstream,
                        audio=audio,
                        transition=transition,
                    )
                )

            results[variant.id] = BlackScreenVariantResult(
                variant=variant,
                detection_results=detection_results,
                events=events,
            )

        _classify_events(
            results=results,
            local_evidence=local_evidence,
            classifier=classifier,
        )

        return results

    @staticmethod
    def _has_technical_issue(
        results: dict[str, BlackScreenVariantResult],
    ) -> bool:

        return any(
            analysis.classification
            in {
                TECHNICAL_ERROR,
                SUSPECTED_TECHNICAL_BLACK,
            }
            for variant_result in results.values()
            for analysis in variant_result.event_analyses
        )

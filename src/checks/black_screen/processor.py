from checks.black_screen.event_store import (
    BlackEventStore,
)
from core.segment_processor import (
    SegmentProcessOutcome,
)
from detectors.black_screen import (
    BlackScreenDetector,
)
from models.detection import (
    BlackDetectionResult,
)
from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
)
from models.segment import Segment


class BlackScreenSegmentProcessor:

    name = "black_screen"
    analysis_profile = "video_realtime"
    requirements = frozenset(
        {AnalysisRequirement.BLACK_INTERVALS}
    )

    @staticmethod
    def supports_segment(
        segment: Segment,
    ) -> bool:
        return segment.has_video

    def __init__(
        self,
        event_store: BlackEventStore,
        detector: BlackScreenDetector | None = None,
    ):
        self.event_store = event_store

        self.detector = (
            detector
            or BlackScreenDetector()
        )

    def process(
        self,
        segment: Segment,
        analysis: SegmentAnalysisBundle,
    ) -> SegmentProcessOutcome:

        result = self.detector.detect(
            segment=segment,
            analysis=analysis,
        )

        if not result.checked:
            error = (
                result.error
                or "black detection failed"
            )

            if result.retryable:
                return SegmentProcessOutcome.retry(
                    error
                )

            return SegmentProcessOutcome.terminal(
                error
            )

        return SegmentProcessOutcome.ok(
            payload=result
        )

    def commit(
        self,
        segment: Segment,
        outcome: SegmentProcessOutcome,
    ) -> None:

        result = outcome.payload

        if not isinstance(
            result,
            BlackDetectionResult,
        ):
            raise RuntimeError(
                (
                    "Black processor completed "
                    "without detection result."
                )
            )

        self.event_store.apply(
            segment=segment,
            result=result,
        )

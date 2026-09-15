from checks.macroblocking.event_store import MacroblockingEventStore
from core.segment_processor import SegmentProcessOutcome
from detectors.macroblocking_segment import MacroblockingDetector
from models.analysis import AnalysisRequirement, SegmentAnalysisBundle
from models.macroblocking import MacroblockingDetectionResult
from models.segment import Segment


class MacroblockingSegmentProcessor:
    name = "macroblocking"
    analysis_profile = "video_realtime"
    requirements = frozenset(
        {AnalysisRequirement.MACROBLOCKING_OBSERVATIONS}
    )

    def __init__(
        self,
        event_store: MacroblockingEventStore,
        detector: MacroblockingDetector | None = None,
    ) -> None:
        self.event_store = event_store
        self.detector = detector or MacroblockingDetector()

    @staticmethod
    def supports_segment(segment: Segment) -> bool:
        return segment.has_video

    def process(
        self,
        segment: Segment,
        analysis: SegmentAnalysisBundle,
    ) -> SegmentProcessOutcome:
        result = self.detector.detect(segment=segment, analysis=analysis)
        if not result.checked:
            error = result.error or "macroblocking detection failed"
            if result.retryable:
                return SegmentProcessOutcome.retry(error)
            return SegmentProcessOutcome.terminal(error)
        return SegmentProcessOutcome.ok(payload=result)

    def commit(
        self,
        segment: Segment,
        outcome: SegmentProcessOutcome,
    ) -> None:
        result = outcome.payload
        if not isinstance(result, MacroblockingDetectionResult):
            raise RuntimeError(
                "Macroblocking processor completed without detection result."
            )
        self.event_store.apply(segment=segment, result=result)

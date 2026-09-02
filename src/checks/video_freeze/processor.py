from checks.video_freeze.event_store import VideoFreezeEventStore
from core.segment_processor import SegmentProcessOutcome
from detectors.video_freeze import VideoFreezeDetector
from models.analysis import AnalysisRequirement, SegmentAnalysisBundle
from models.freeze import VideoFreezeDetectionResult
from models.segment import Segment


class VideoFreezeSegmentProcessor:
    name = "video_freeze"
    analysis_profile = "video_realtime"
    requirements = frozenset({AnalysisRequirement.FREEZE_INTERVALS})

    def __init__(
        self,
        event_store: VideoFreezeEventStore,
        detector: VideoFreezeDetector | None = None,
    ) -> None:
        self.event_store = event_store
        self.detector = detector or VideoFreezeDetector()

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
            error = result.error or "video-freeze detection failed"
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
        if not isinstance(result, VideoFreezeDetectionResult):
            raise RuntimeError(
                "Video-freeze processor completed without detection result."
            )
        self.event_store.apply(segment=segment, result=result)

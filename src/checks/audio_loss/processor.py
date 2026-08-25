from checks.audio_loss.event_store import AudioLossEventStore
from core.segment_processor import SegmentProcessOutcome
from detectors.audio_loss import AudioLossDetector
from models.analysis import AnalysisRequirement, SegmentAnalysisBundle
from models.audio import AudioTrackHint
from models.audio_loss import AudioLossDetectionResult
from models.segment import Segment


class AudioLossSegmentProcessor:
    name = "audio_loss"
    analysis_profile = "audio_realtime"
    requirements = frozenset({AnalysisRequirement.SILENCE_INTERVALS})

    def __init__(
        self,
        event_store: AudioLossEventStore,
        detector: AudioLossDetector | None = None,
    ) -> None:
        self.event_store = event_store
        self.detector = detector or AudioLossDetector()

    @staticmethod
    def supports_segment(segment: Segment) -> bool:
        return segment.audio_track_hint is not AudioTrackHint.EXTERNAL

    def process(
        self,
        segment: Segment,
        analysis: SegmentAnalysisBundle,
    ) -> SegmentProcessOutcome:
        result = self.detector.detect(segment=segment, analysis=analysis)
        if not result.checked:
            error = result.error or "audio-loss detection failed"
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
        if not isinstance(result, AudioLossDetectionResult):
            raise RuntimeError(
                "Audio-loss processor completed without detection result."
            )
        self.event_store.apply(segment=segment, result=result)

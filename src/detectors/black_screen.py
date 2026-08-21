from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
)
from models.detection import (
    BlackDetectionResult,
    BlackInterval,
)
from models.segment import Segment


class BlackScreenDetector:

    def detect(
        self,
        *,
        segment: Segment,
        analysis: SegmentAnalysisBundle,
    ) -> BlackDetectionResult:

        video = analysis.require_video_realtime()

        if not video.checked:
            return BlackDetectionResult(
                variant_id=segment.variant_id,
                sequence=segment.sequence,
                segment_uri=segment.uri,
                segment_duration=(
                    segment.duration
                ),
                program_date_time=(
                    segment.program_date_time
                ),
                checked=False,
                error=(
                    video.error
                    or "video analysis failed"
                ),
                retryable=video.retryable,
            )

        intervals = video.require_output(
            AnalysisRequirement.BLACK_INTERVALS,
            tuple,
        )
        if not all(isinstance(item, BlackInterval) for item in intervals):
            raise TypeError("Invalid black_intervals analysis output")

        return BlackDetectionResult(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            program_date_time=(
                segment.program_date_time
            ),
            checked=True,
            error=None,
            black_intervals=list(intervals),
        )

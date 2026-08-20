from models.analysis import (
    SegmentAnalysisBundle,
)
from models.detection import (
    BlackDetectionResult,
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
            black_intervals=list(
                video.black_intervals
            ),
        )

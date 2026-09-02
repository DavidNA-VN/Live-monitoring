from models.analysis import AnalysisRequirement, SegmentAnalysisBundle
from models.freeze import FreezeInterval, VideoFreezeDetectionResult
from models.segment import Segment


class VideoFreezeDetector:
    def detect(
        self,
        *,
        segment: Segment,
        analysis: SegmentAnalysisBundle,
    ) -> VideoFreezeDetectionResult:
        video = analysis.require_video_realtime()
        if not video.checked:
            return self._result(
                segment,
                checked=False,
                error=video.error or "video analysis failed",
                retryable=video.retryable,
            )

        intervals = video.require_output(
            AnalysisRequirement.FREEZE_INTERVALS,
            tuple,
        )
        if not all(isinstance(item, FreezeInterval) for item in intervals):
            raise TypeError("Invalid freeze_intervals analysis output")

        return self._result(segment, intervals=list(intervals))

    @staticmethod
    def _result(
        segment: Segment,
        *,
        checked: bool = True,
        error: str | None = None,
        retryable: bool = True,
        intervals: list[FreezeInterval] | None = None,
    ) -> VideoFreezeDetectionResult:
        return VideoFreezeDetectionResult(
            variant_id=segment.variant_id,
            sequence=segment.sequence,
            segment_uri=segment.uri,
            segment_duration=segment.duration,
            program_date_time=segment.program_date_time,
            checked=checked,
            error=error,
            retryable=retryable,
            freeze_intervals=list(intervals or ()),
        )

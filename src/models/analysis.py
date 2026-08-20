from dataclasses import dataclass
from enum import Enum

from models.detection import BlackInterval


class AnalysisRequirement(str, Enum):
    BLACK_INTERVALS = "black_intervals"


@dataclass(frozen=True)
class VideoRealtimeAnalysis:
    checked: bool
    error: str | None = None
    retryable: bool = True
    black_intervals: tuple[BlackInterval, ...] = ()
    timed_out: bool = False


@dataclass(frozen=True)
class SegmentAnalysisBundle:
    profile_name: str
    video_realtime: VideoRealtimeAnalysis | None = None

    def require_video_realtime(self) -> VideoRealtimeAnalysis:
        if self.video_realtime is None:
            raise ValueError(
                "Analysis bundle has no video_realtime result"
            )

        return self.video_realtime

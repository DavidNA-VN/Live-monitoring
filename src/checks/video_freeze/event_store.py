from typing import Protocol

from models.freeze import VideoFreezeDetectionResult
from models.segment import Segment


class VideoFreezeEventStore(Protocol):
    def apply(
        self,
        *,
        segment: Segment,
        result: VideoFreezeDetectionResult,
    ) -> None:
        ...

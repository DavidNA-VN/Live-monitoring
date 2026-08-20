from enum import Enum
from typing import Protocol

from models.analysis import (
    AnalysisRequirement,
    SegmentAnalysisBundle,
)
from models.segment import Segment


class AnalysisResourceClass(str, Enum):
    METADATA = "metadata"
    VIDEO_DECODE = "video_decode"
    AUDIO_DECODE = "audio_decode"
    EXPENSIVE = "expensive"


class AnalysisProfile(Protocol):
    name: str
    provides: frozenset[AnalysisRequirement]
    resource_class: AnalysisResourceClass

    def supports_segment(self, segment: Segment) -> bool:
        ...

    def analyze(
        self,
        segment: Segment,
    ) -> SegmentAnalysisBundle:
        ...

    def close(self) -> None:
        ...

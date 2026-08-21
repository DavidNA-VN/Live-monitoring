from typing import Protocol

from models.analysis import (
    AnalysisRequirement,
    AnalysisResourceClass,
    SegmentAnalysisBundle,
)
from models.segment import Segment


class AnalysisProfile(Protocol):
    name: str
    provides: frozenset[AnalysisRequirement]
    resource_class: AnalysisResourceClass

    def supports_segment(self, segment: Segment) -> bool:
        ...

    def analyze(
        self,
        segment: Segment,
        *,
        requirements: frozenset[AnalysisRequirement],
    ) -> SegmentAnalysisBundle:
        ...

    def close(self) -> None:
        ...

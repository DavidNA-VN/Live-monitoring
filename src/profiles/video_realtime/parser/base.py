from typing import Protocol

from models.analysis import AnalysisRequirement
from models.segment import Segment


class VideoFilterParser(Protocol):
    requirement: AnalysisRequirement

    @property
    def filter_expression(self) -> str:
        ...

    def parse(self, *, ffmpeg_output: str, segment: Segment) -> object:
        ...

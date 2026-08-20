from typing import Protocol

from models.detection import BlackDetectionResult
from models.segment import Segment


class BlackEventStore(Protocol):
    def apply(
        self,
        *,
        segment: Segment,
        result: BlackDetectionResult,
    ) -> None:
        ...

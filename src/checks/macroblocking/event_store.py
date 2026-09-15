from typing import Protocol

from models.macroblocking import MacroblockingDetectionResult
from models.segment import Segment


class MacroblockingEventStore(Protocol):
    def apply(
        self,
        *,
        segment: Segment,
        result: MacroblockingDetectionResult,
    ) -> None:
        ...

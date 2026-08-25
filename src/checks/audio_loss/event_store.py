from typing import Protocol

from models.audio_loss import AudioLossDetectionResult
from models.segment import Segment


class AudioLossEventStore(Protocol):
    def apply(
        self,
        *,
        segment: Segment,
        result: AudioLossDetectionResult,
    ) -> None:
        ...

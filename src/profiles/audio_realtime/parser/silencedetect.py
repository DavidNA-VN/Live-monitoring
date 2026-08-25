import math
import re

from models.analysis import AnalysisRequirement
from models.audio import SilenceInterval
from models.segment import Segment


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
SILENCE_BOUNDARY_PATTERN = re.compile(
    rf"silence_(start|end):\s*({_NUMBER})"
)


class SilencedetectParser:
    requirement = AnalysisRequirement.SILENCE_INTERVALS

    def __init__(
        self,
        *,
        threshold_dbfs: float = -60.0,
        minimum_duration: float = 0.1,
    ) -> None:
        if not math.isfinite(threshold_dbfs) or threshold_dbfs > 0:
            raise ValueError("threshold_dbfs must be finite and <= 0")
        if not math.isfinite(minimum_duration) or minimum_duration <= 0:
            raise ValueError("minimum_duration must be finite and > 0")
        self.threshold_dbfs = threshold_dbfs
        self.minimum_duration = minimum_duration

    @property
    def filter_expression(self) -> str:
        return (
            "silencedetect="
            f"noise={self.threshold_dbfs:g}dB:"
            f"d={self.minimum_duration:g}:"
            "mono=0"
        )

    def parse(
        self,
        *,
        ffmpeg_output: str,
        segment: Segment,
    ) -> tuple[SilenceInterval, ...]:
        intervals: list[SilenceInterval] = []
        current_start: float | None = None

        for match in SILENCE_BOUNDARY_PATTERN.finditer(ffmpeg_output):
            boundary, raw_value = match.groups()
            value = float(raw_value)
            if boundary == "start":
                if current_start is None:
                    current_start = value
                continue
            if current_start is None:
                continue
            self._append_interval(
                intervals,
                start=current_start,
                end=value,
                segment_duration=segment.duration,
            )
            current_start = None

        if current_start is not None:
            self._append_interval(
                intervals,
                start=current_start,
                end=segment.duration,
                segment_duration=segment.duration,
            )

        return tuple(intervals)

    @staticmethod
    def _append_interval(
        intervals: list[SilenceInterval],
        *,
        start: float,
        end: float,
        segment_duration: float,
    ) -> None:
        interval = SilenceInterval(
            start=min(segment_duration, max(0.0, start)),
            end=min(segment_duration, max(0.0, end)),
        )
        if interval.duration > 0:
            intervals.append(interval)

import re

from models.analysis import AnalysisRequirement
from models.detection import BlackInterval
from models.segment import Segment


BLACK_START_PATTERN = re.compile(r"black_start:([0-9]+(?:\.[0-9]+)?)")
BLACK_END_PATTERN = re.compile(r"black_end:([0-9]+(?:\.[0-9]+)?)")


class BlackdetectParser:
    requirement = AnalysisRequirement.BLACK_INTERVALS

    def __init__(self, *, pix_th: float = 0.10, pic_th: float = 0.98) -> None:
        if not 0.0 <= pix_th <= 1.0:
            raise ValueError("pix_th must be between 0 and 1")
        if not 0.0 <= pic_th <= 1.0:
            raise ValueError("pic_th must be between 0 and 1")
        self.pix_th = pix_th
        self.pic_th = pic_th

    @property
    def filter_expression(self) -> str:
        return (
            "blackdetect="
            "d=0:"
            f"pix_th={self.pix_th}:"
            f"pic_th={self.pic_th}"
        )

    def parse(
        self,
        *,
        ffmpeg_output: str,
        segment: Segment,
    ) -> tuple[BlackInterval, ...]:
        intervals: list[BlackInterval] = []
        current_start: float | None = None

        for line in ffmpeg_output.splitlines():
            start_match = BLACK_START_PATTERN.search(line)
            end_match = BLACK_END_PATTERN.search(line)
            if start_match:
                current_start = float(start_match.group(1))
            if end_match and current_start is not None:
                interval = BlackInterval(
                    start=max(0.0, current_start),
                    end=min(segment.duration, float(end_match.group(1))),
                )
                if interval.duration > 0:
                    intervals.append(interval)
                current_start = None

        if current_start is not None:
            interval = BlackInterval(
                start=max(0.0, current_start),
                end=segment.duration,
            )
            if interval.duration > 0:
                intervals.append(interval)
        return tuple(intervals)

from __future__ import annotations

import math
import re

from models.analysis import AnalysisRequirement
from models.freeze import FreezeInterval
from models.segment import Segment


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
FREEZE_START_PATTERN = re.compile(rf"freeze_start:\s*({_NUMBER})")
FREEZE_END_PATTERN = re.compile(rf"freeze_end:\s*({_NUMBER})")


class FreezedetectParser:
    requirement = AnalysisRequirement.FREEZE_INTERVALS

    def __init__(
        self,
        *,
        noise_db: float = -60.0,
        minimum_duration: float = 0.2,
    ) -> None:
        if not math.isfinite(noise_db) or noise_db > 0:
            raise ValueError("noise_db must be finite and <= 0")
        if not math.isfinite(minimum_duration) or minimum_duration <= 0:
            raise ValueError("minimum_duration must be finite and > 0")
        self.noise_db = noise_db
        self.minimum_duration = minimum_duration

    @property
    def filter_expression(self) -> str:
        return (
            "freezedetect="
            f"n={self.noise_db:g}dB:"
            f"d={self.minimum_duration:g}"
        )

    def parse(
        self,
        *,
        ffmpeg_output: str,
        segment: Segment,
    ) -> tuple[FreezeInterval, ...]:
        intervals: list[FreezeInterval] = []
        current_start: float | None = None

        for line in ffmpeg_output.splitlines():
            start_match = FREEZE_START_PATTERN.search(line)
            end_match = FREEZE_END_PATTERN.search(line)
            if start_match:
                current_start = float(start_match.group(1))
            if end_match and current_start is not None:
                self._append_interval(
                    intervals,
                    start=current_start,
                    end=float(end_match.group(1)),
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
        intervals: list[FreezeInterval],
        *,
        start: float,
        end: float,
        segment_duration: float,
    ) -> None:
        bounded_start = max(0.0, start)
        bounded_end = min(segment_duration, end)
        if bounded_end <= bounded_start:
            return
        intervals.append(FreezeInterval(bounded_start, bounded_end))

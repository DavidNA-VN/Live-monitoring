from __future__ import annotations

import math
from collections.abc import Iterable

from models.detection import BlackInterval
from models.frame_fingerprint import BoundaryFrameFingerprint
from models.freeze import FreezeInterval


class FreezeEvidenceAssembler:
    def __init__(
        self,
        *,
        minimum_duration: float,
        boundary_tolerance: float = 0.10,
    ) -> None:
        if not math.isfinite(minimum_duration) or minimum_duration <= 0:
            raise ValueError("minimum_duration must be finite and > 0")
        if not math.isfinite(boundary_tolerance) or boundary_tolerance < 0:
            raise ValueError("boundary_tolerance must be finite and >= 0")
        self.minimum_duration = minimum_duration
        self.boundary_tolerance = boundary_tolerance

    def assemble(
        self,
        *,
        raw_freeze_intervals: Iterable[FreezeInterval],
        black_intervals: Iterable[BlackInterval],
        segment_duration: float,
        first_frame: BoundaryFrameFingerprint,
        last_frame: BoundaryFrameFingerprint,
    ) -> tuple[FreezeInterval, ...]:
        if not math.isfinite(segment_duration) or segment_duration <= 0:
            raise ValueError("segment_duration must be finite and > 0")

        black = sorted(black_intervals, key=lambda item: item.start)
        effective: list[FreezeInterval] = []
        raw_ranges = self._merge_ranges(
            (item.start, item.end) for item in raw_freeze_intervals
        )
        for raw_start, raw_end in raw_ranges:
            pieces = [(raw_start, raw_end)]
            for excluded in black:
                pieces = self._subtract(pieces, excluded)
                if not pieces:
                    break
            for start, end in pieces:
                if end - start < self.minimum_duration:
                    continue
                effective.append(
                    FreezeInterval(
                        start=start,
                        end=end,
                        start_boundary_fingerprint=(
                            first_frame
                            if start <= self.boundary_tolerance
                            else None
                        ),
                        end_boundary_fingerprint=(
                            last_frame
                            if end >= segment_duration - self.boundary_tolerance
                            else None
                        ),
                    )
                )
        return tuple(effective)

    @staticmethod
    def _merge_ranges(
        ranges: Iterable[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        merged: list[tuple[float, float]] = []
        for start, end in sorted(ranges):
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
                continue
            prior_start, prior_end = merged[-1]
            merged[-1] = (prior_start, max(prior_end, end))
        return merged

    @staticmethod
    def _subtract(
        pieces: list[tuple[float, float]],
        excluded: BlackInterval,
    ) -> list[tuple[float, float]]:
        remaining: list[tuple[float, float]] = []
        for start, end in pieces:
            if excluded.end <= start or excluded.start >= end:
                remaining.append((start, end))
                continue
            if excluded.start > start:
                remaining.append((start, min(end, excluded.start)))
            if excluded.end < end:
                remaining.append((max(start, excluded.end), end))
        return remaining

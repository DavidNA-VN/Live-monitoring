from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from models.frame_fingerprint import BoundaryFrameFingerprint


GRAY32_MAE_V1 = "gray32-mae-v1"
FINGERPRINT_WIDTH = 32
FINGERPRINT_HEIGHT = 32


class FrameComparisonStatus(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class FrameComparison:
    status: FrameComparisonStatus
    normalized_mae: float | None = None


class FrameFingerprintMatcher(Protocol):
    def compare(
        self,
        left: BoundaryFrameFingerprint,
        right: BoundaryFrameFingerprint,
    ) -> FrameComparison: ...


class NormalizedMaeFrameMatcher:
    def __init__(self, *, threshold: float = 0.02) -> None:
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("MAE threshold must be finite and between 0 and 1")
        self.threshold = threshold

    def compare(self, left, right) -> FrameComparison:
        if (
            left.algorithm != right.algorithm
            or left.width != right.width
            or left.height != right.height
        ):
            return FrameComparison(FrameComparisonStatus.INCOMPATIBLE)
        if left.is_black or right.is_black:
            return FrameComparison(FrameComparisonStatus.INCOMPATIBLE)
        error = sum(abs(a - b) for a, b in zip(left.pixels, right.pixels))
        normalized_mae = error / (len(left.pixels) * 255.0)
        return FrameComparison(
            FrameComparisonStatus.MATCH
            if normalized_mae <= self.threshold
            else FrameComparisonStatus.MISMATCH,
            normalized_mae,
        )


def make_gray_fingerprint(
    pixels: bytes,
    *,
    width: int = FINGERPRINT_WIDTH,
    height: int = FINGERPRINT_HEIGHT,
    black_pixel_threshold: float = 0.10,
    black_picture_ratio: float = 0.98,
) -> BoundaryFrameFingerprint:
    if width != FINGERPRINT_WIDTH or height != FINGERPRINT_HEIGHT:
        raise ValueError("gray32-mae-v1 fingerprints must be 32x32")
    for name, value in (
        ("black_pixel_threshold", black_pixel_threshold),
        ("black_picture_ratio", black_picture_ratio),
    ):
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be finite and between 0 and 1")
    expected = width * height
    if len(pixels) != expected:
        raise ValueError("pixel length does not match fingerprint dimensions")
    maximum_black_luma = round(black_pixel_threshold * 255)
    black_pixels = sum(value <= maximum_black_luma for value in pixels)
    return BoundaryFrameFingerprint(
        algorithm=GRAY32_MAE_V1,
        width=width,
        height=height,
        pixels=pixels,
        is_black=(black_pixels / expected) >= black_picture_ratio,
    )


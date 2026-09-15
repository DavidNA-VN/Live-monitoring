from __future__ import annotations

import math

import pytest

from models.frame_fingerprint import BoundaryFrameFingerprint
from core.frame_similarity import (
    GRAY32_MAE_V1,
    FrameComparisonStatus,
    NormalizedMaeFrameMatcher,
    make_gray_fingerprint,
)


def fingerprint(value: int, *, size: int = 4, algorithm=GRAY32_MAE_V1):
    return BoundaryFrameFingerprint(
        algorithm=algorithm,
        width=size,
        height=size,
        pixels=bytes([value] * (size * size)),
        is_black=False,
    )


def test_identical_frames_match_with_zero_mae():
    comparison = NormalizedMaeFrameMatcher().compare(
        fingerprint(100), fingerprint(100)
    )
    assert comparison.status is FrameComparisonStatus.MATCH
    assert comparison.normalized_mae == 0.0


def test_mae_threshold_is_inclusive():
    matcher = NormalizedMaeFrameMatcher(threshold=5 / 255)
    comparison = matcher.compare(fingerprint(100), fingerprint(105))
    assert comparison.status is FrameComparisonStatus.MATCH
    assert comparison.normalized_mae == pytest.approx(5 / 255)


def test_visibly_different_frames_do_not_match():
    comparison = NormalizedMaeFrameMatcher(threshold=0.02).compare(
        fingerprint(40), fingerprint(200)
    )
    assert comparison.status is FrameComparisonStatus.MISMATCH
    assert comparison.normalized_mae == pytest.approx(160 / 255)


@pytest.mark.parametrize(
    "right",
    [
        fingerprint(100, size=8),
        fingerprint(100, algorithm="gray32-mae-v2"),
    ],
)
def test_incompatible_fingerprints_fail_closed(right):
    comparison = NormalizedMaeFrameMatcher().compare(fingerprint(100), right)
    assert comparison.status is FrameComparisonStatus.INCOMPATIBLE
    assert comparison.normalized_mae is None


def test_black_frame_is_not_eligible_for_freeze_join():
    black = make_gray_fingerprint(bytes([0] * (32 * 32)))
    ordinary = make_gray_fingerprint(bytes([100] * (32 * 32)))
    assert black.is_black is True
    assert ordinary.is_black is False
    assert (
        NormalizedMaeFrameMatcher().compare(black, black).status
        is FrameComparisonStatus.INCOMPATIBLE
    )


@pytest.mark.parametrize("threshold", [-0.1, 1.1, math.nan, math.inf])
def test_invalid_matcher_threshold_is_rejected(threshold):
    with pytest.raises(ValueError, match="threshold"):
        NormalizedMaeFrameMatcher(threshold=threshold)


def test_fingerprint_rejects_wrong_pixel_length():
    with pytest.raises(ValueError, match="pixel length"):
        BoundaryFrameFingerprint(
            algorithm=GRAY32_MAE_V1,
            width=32,
            height=32,
            pixels=b"short",
            is_black=False,
        )


def test_gray32_factory_rejects_a_mislabeled_size():
    with pytest.raises(ValueError, match="32x32"):
        make_gray_fingerprint(bytes(16 * 16), width=16, height=16)


def test_fingerprint_requires_boolean_black_marker():
    with pytest.raises(TypeError, match="boolean"):
        BoundaryFrameFingerprint(
            algorithm=GRAY32_MAE_V1,
            width=4,
            height=4,
            pixels=bytes(16),
            is_black=1,
        )

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class AxisGridScore:
    confidence: float
    support_ratio: float
    period: int | None
    phase: int | None
    boundary_strength: float = 0.0


@dataclass(frozen=True)
class LocalGridScore:
    confidence: float
    support_ratio: float


def _score_axis(
    strengths: NDArray[np.float32],
    *,
    minimum_period: int,
    maximum_period: int,
    candidate_periods: tuple[int, ...] | None = None,
) -> AxisGridScore:
    length = strengths.size
    if length < minimum_period * 2:
        return AxisGridScore(0.0, 0.0, None, None)
    baseline = float(np.median(strengths))
    spread = float(np.median(np.abs(strengths - baseline))) + 1.0
    best = AxisGridScore(0.0, 0.0, None, None)
    upper = min(maximum_period, max(minimum_period, length // 2))
    indexes = np.arange(length, dtype=np.int32)
    neighbor_baseline = (
        np.roll(strengths, 1) + np.roll(strengths, -1)
    ) / 2.0
    suspicious_all = strengths > neighbor_baseline + max(2.0, 0.5 * spread)
    periods = (
        tuple(
            period
            for period in candidate_periods
            if minimum_period <= period <= upper
        )
        if candidate_periods is not None
        else range(minimum_period, upper + 1)
    )
    for period in periods:
        phases = indexes % period
        counts = np.bincount(phases, minlength=period).astype(np.float32)
        eligible = counts >= 2
        if not np.any(eligible):
            continue
        sums = np.bincount(
            phases,
            weights=strengths,
            minlength=period,
        ).astype(np.float32)
        suspicious_counts = np.bincount(
            phases,
            weights=suspicious_all.astype(np.float32),
            minlength=period,
        ).astype(np.float32)
        grid_means = np.divide(sums, counts, out=np.zeros_like(sums), where=eligible)
        neighbor_sums = np.bincount(
            phases,
            weights=neighbor_baseline,
            minlength=period,
        ).astype(np.float32)
        surroundings = np.divide(
            neighbor_sums,
            counts,
            out=np.full_like(sums, baseline),
            where=eligible,
        )
        contrasts = np.maximum(
            0.0,
            (grid_means - surroundings) / (grid_means + 8.0),
        )
        adjacent_means = np.maximum(
            np.roll(grid_means, 1),
            np.roll(grid_means, -1),
        )
        phase_sharpness = np.maximum(
            0.0,
            (grid_means - adjacent_means) / (grid_means + 8.0),
        )
        supports = np.divide(
            suspicious_counts,
            counts,
            out=np.zeros_like(sums),
            where=eligible,
        )
        confidences = np.minimum(
            1.0,
            contrasts
            * (0.35 + 0.65 * supports)
            * (0.20 + 0.80 * phase_sharpness)
            * (0.25 + 0.75 * np.minimum(1.0, grid_means / 24.0))
            * 3.0,
        )
        confidences[~eligible] = 0.0
        best_confidence = float(np.max(confidences))
        tied = np.flatnonzero(np.isclose(confidences, best_confidence))
        phase = int(tied[np.argmax(supports[tied])])
        support = float(supports[phase])
        if best_confidence > best.confidence or (
            math.isclose(best_confidence, best.confidence)
            and support > best.support_ratio
        ):
            best = AxisGridScore(
                best_confidence,
                support,
                period,
                phase,
                float(grid_means[phase]),
            )
    return best


def score_local_window(
    horizontal_diff: NDArray[np.float32],
    vertical_diff: NDArray[np.float32],
    *,
    minimum_period: int,
    maximum_period: int,
    candidate_periods: tuple[int, ...] | None = None,
) -> LocalGridScore:
    column_strength = np.mean(horizontal_diff, axis=0, dtype=np.float32)
    row_strength = np.mean(vertical_diff, axis=1, dtype=np.float32)
    horizontal_grid = _score_axis(
        column_strength,
        minimum_period=minimum_period,
        maximum_period=maximum_period,
        candidate_periods=candidate_periods,
    )
    vertical_grid = _score_axis(
        row_strength,
        minimum_period=minimum_period,
        maximum_period=maximum_period,
        candidate_periods=candidate_periods,
    )
    confidence = max(horizontal_grid.confidence, vertical_grid.confidence)
    support = max(horizontal_grid.support_ratio, vertical_grid.support_ratio)
    texture = float(
        np.mean(horizontal_diff, dtype=np.float32)
        + np.mean(vertical_diff, dtype=np.float32)
    ) / 2.0
    # Thick overlays and natural grids carry sustained edge energy around a
    # boundary. Codec blocking more often has a sharp boundary with relatively
    # quiet block interiors, so suppress high-energy windows before fusion.
    texture_mask = 1.0 / (1.0 + max(0.0, texture - 3.0) / 4.0)
    return LocalGridScore(
        confidence=max(0.0, min(1.0, confidence * texture_mask)),
        support_ratio=max(0.0, min(1.0, support)),
    )

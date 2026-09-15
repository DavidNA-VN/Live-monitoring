from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from models.macroblocking import MacroblockingFusionStrategy


FloatMap = NDArray[np.float32]


def fuse_scale_maps(
    maps: tuple[FloatMap, ...],
    *,
    strategy: MacroblockingFusionStrategy,
    support_threshold: float,
) -> FloatMap:
    if not maps:
        raise ValueError("at least one scale map is required")
    shape = maps[0].shape
    if any(item.shape != shape for item in maps):
        raise ValueError("all scale maps must have the same shape")
    stacked = np.stack(maps).astype(np.float32, copy=False)
    maximum = np.max(stacked, axis=0)
    if strategy is MacroblockingFusionStrategy.MAX:
        return maximum
    if strategy is MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY:
        agreement = np.mean(stacked >= support_threshold, axis=0)
        factor = 0.70 + 0.30 * agreement
        return (maximum * factor).astype(np.float32)
    if strategy is MacroblockingFusionStrategy.TOP_TWO_WEIGHTED:
        if len(maps) == 1:
            return maximum
        ordered = np.sort(stacked, axis=0)
        return (ordered[-1] * 0.70 + ordered[-2] * 0.30).astype(np.float32)
    raise ValueError(f"Unsupported fusion strategy: {strategy}")

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


FloatMap = NDArray[np.float32]


@dataclass(frozen=True)
class DifferenceMaps:
    horizontal: FloatMap
    vertical: FloatMap
    frame_std: float


def build_difference_maps(frame: NDArray[np.generic]) -> DifferenceMaps:
    if not isinstance(frame, np.ndarray) or frame.ndim != 2:
        raise ValueError("luminance frame must be a two-dimensional ndarray")
    if frame.shape[0] < 2 or frame.shape[1] < 2:
        raise ValueError("luminance frame must be at least 2x2")
    if not np.issubdtype(frame.dtype, np.number):
        raise TypeError("luminance frame dtype must be numeric")
    values = frame.astype(np.float32, copy=False)
    if not np.isfinite(values).all():
        raise ValueError("luminance frame must contain finite values")
    return DifferenceMaps(
        horizontal=np.abs(np.diff(values, axis=1)),
        vertical=np.abs(np.diff(values, axis=0)),
        frame_std=float(np.std(values)),
    )

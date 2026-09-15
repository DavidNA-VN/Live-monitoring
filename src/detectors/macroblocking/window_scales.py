from __future__ import annotations

from dataclasses import dataclass

from models.macroblocking import MacroblockingAnalyzerConfig


@dataclass(frozen=True)
class WindowScale:
    size: int
    stride: int


def plan_window_scales(
    width: int,
    height: int,
    config: MacroblockingAnalyzerConfig,
) -> tuple[WindowScale, ...]:
    limit = min(width, height)
    sizes: list[int] = []
    for divisor in config.relative_scale_divisors:
        proposed = max(config.minimum_period * 3, round(width / divisor))
        size = min(limit, proposed)
        if size not in sizes:
            sizes.append(size)
    if not sizes:
        raise ValueError("frame is too small for configured window scales")
    return tuple(
        WindowScale(
            size=size,
            stride=max(1, round(size * config.stride_ratio)),
        )
        for size in sorted(sizes)
    )


def window_origins(length: int, size: int, stride: int) -> tuple[int, ...]:
    if size > length:
        return ()
    origins = list(range(0, length - size + 1, stride))
    final = length - size
    if not origins or origins[-1] != final:
        origins.append(final)
    return tuple(origins)

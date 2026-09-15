from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class _Run:
    y: int
    start: int
    end: int
    label: int


def _row_runs(row: NDArray[np.bool_]) -> tuple[tuple[int, int], ...]:
    padded = np.pad(row.astype(np.int8, copy=False), (1, 1))
    transitions = np.diff(padded)
    starts = np.flatnonzero(transitions == 1)
    ends = np.flatnonzero(transitions == -1) - 1
    return tuple(zip(starts.tolist(), ends.tolist()))


def filter_regions(
    mask: NDArray[np.bool_],
    *,
    minimum_area: int,
    fill_minimum_bbox_area: int | None = None,
    fill_minimum_density: float = 1.0,
) -> tuple[NDArray[np.bool_], int]:
    if mask.ndim != 2:
        raise ValueError("region mask must be two-dimensional")
    if minimum_area <= 0:
        raise ValueError("minimum_area must be > 0")
    if fill_minimum_bbox_area is not None and fill_minimum_bbox_area <= 0:
        raise ValueError("fill_minimum_bbox_area must be > 0")
    if not 0.0 <= fill_minimum_density <= 1.0:
        raise ValueError("fill_minimum_density must be between 0 and 1")
    parents: list[int] = []
    runs: list[_Run] = []
    previous: list[_Run] = []

    def find(label: int) -> int:
        root = label
        while parents[root] != root:
            root = parents[root]
        while parents[label] != label:
            parent = parents[label]
            parents[label] = root
            label = parent
        return root

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for y, row in enumerate(mask):
        current: list[_Run] = []
        previous_index = 0
        for start, end in _row_runs(row):
            while (
                previous_index < len(previous)
                and previous[previous_index].end < start
            ):
                previous_index += 1
            overlapping: list[_Run] = []
            scan = previous_index
            while scan < len(previous) and previous[scan].start <= end:
                overlapping.append(previous[scan])
                scan += 1
            if overlapping:
                label = find(overlapping[0].label)
                for item in overlapping[1:]:
                    union(label, item.label)
                label = find(label)
            else:
                label = len(parents)
                parents.append(label)
            run = _Run(y=y, start=start, end=end, label=label)
            current.append(run)
            runs.append(run)
        previous = current

    components: dict[int, list[int]] = {}
    for index, run in enumerate(runs):
        components.setdefault(find(run.label), []).append(index)

    retained = np.zeros_like(mask, dtype=np.bool_)
    region_count = 0
    for indexes in components.values():
        component_runs = [runs[index] for index in indexes]
        area = sum(item.end - item.start + 1 for item in component_runs)
        if area < minimum_area:
            continue
        region_count += 1
        minimum_y = min(item.y for item in component_runs)
        maximum_y = max(item.y for item in component_runs)
        minimum_x = min(item.start for item in component_runs)
        maximum_x = max(item.end for item in component_runs)
        bounding_area = (
            (maximum_y - minimum_y + 1) * (maximum_x - minimum_x + 1)
        )
        if (
            fill_minimum_bbox_area is not None
            and bounding_area >= fill_minimum_bbox_area
            and area / bounding_area >= fill_minimum_density
        ):
            retained[
                minimum_y : maximum_y + 1,
                minimum_x : maximum_x + 1,
            ] = True
        else:
            for item in component_runs:
                retained[item.y, item.start : item.end + 1] = True
    return retained, region_count

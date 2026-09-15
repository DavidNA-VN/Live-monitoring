from __future__ import annotations

import numpy as np
import pytest

from detectors.macroblocking import MacroblockingAnalyzer
from detectors.macroblocking.difference_maps import build_difference_maps
from detectors.macroblocking.grid_score import _score_axis
from detectors.macroblocking.regions import filter_regions
from detectors.macroblocking.scale_fusion import fuse_scale_maps
from detectors.macroblocking.window_scales import plan_window_scales, window_origins
from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFusionStrategy,
)


def _config(**overrides) -> MacroblockingAnalyzerConfig:
    values = {
        "fusion_strategy": MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY,
        "analysis_width": 128,
        "analysis_height": 96,
        "relative_scale_divisors": (16, 8, 4),
    }
    values.update(overrides)
    return MacroblockingAnalyzerConfig(**values)


def test_difference_maps_are_absolute_luminance_differences():
    frame = np.array([[1, 4, 2], [7, 3, 9]], dtype=np.uint8)
    maps = build_difference_maps(frame)
    np.testing.assert_array_equal(maps.horizontal, [[3, 2], [4, 6]])
    np.testing.assert_array_equal(maps.vertical, [[6, 1, 7]])


def test_window_planner_is_resolution_aware_and_covers_odd_border():
    scales = plan_window_scales(127, 95, _config())
    assert [item.size for item in scales] == [8, 16, 32]
    assert window_origins(95, 32, 16)[-1] == 63


def test_grid_search_prefers_higher_support_when_confidence_is_tied():
    strengths = np.zeros(63, dtype=np.float32)
    strengths[7::8] = 100.0
    score = _score_axis(strengths, minimum_period=2, maximum_period=16)
    assert score.period == 8
    assert score.support_ratio == pytest.approx(1.0)


def test_fusion_does_not_average_away_local_scale_evidence():
    maps = (
        np.full((2, 2), 0.9, dtype=np.float32),
        np.full((2, 2), 0.2, dtype=np.float32),
        np.full((2, 2), 0.1, dtype=np.float32),
    )
    maximum = fuse_scale_maps(
        maps,
        strategy=MacroblockingFusionStrategy.MAX,
        support_threshold=0.5,
    )
    consistent = fuse_scale_maps(
        maps,
        strategy=MacroblockingFusionStrategy.MAX_WITH_CONSISTENCY,
        support_threshold=0.5,
    )
    assert maximum[0, 0] == pytest.approx(0.9)
    assert 0.63 < consistent[0, 0] < maximum[0, 0]


def test_region_filter_removes_small_island_and_retains_union_area():
    mask = np.zeros((8, 8), dtype=np.bool_)
    mask[0, 0] = True
    mask[3:6, 2:6] = True
    retained, count = filter_regions(mask, minimum_area=4)
    assert count == 1
    assert int(retained.sum()) == 12


def test_region_filter_reconstructs_dense_large_region_only():
    mask = np.zeros((20, 20), dtype=np.bool_)
    mask[2:12, 2:12] = True
    mask[5:9, 5:9] = False
    retained, count = filter_regions(
        mask,
        minimum_area=4,
        fill_minimum_bbox_area=80,
        fill_minimum_density=0.5,
    )
    assert count == 1
    assert int(retained.sum()) == 100


def test_uniform_frame_is_an_invalid_observation():
    observation = MacroblockingAnalyzer(_config()).analyze(
        np.full((95, 127), 80, dtype=np.uint8),
        offset_seconds=1.25,
    )
    assert not isinstance(observation, tuple)
    assert observation.valid is False
    assert observation.invalid_reason == "insufficient_luminance_variation"
    assert observation.affected_area_ratio == 0.0


def test_periodic_block_region_produces_spatial_evidence():
    y, x = np.indices((64, 64))
    frame = np.tile(np.arange(128, dtype=np.uint8), (96, 1))
    frame[16:80, 32:96] = (((x // 8 + y // 8) % 2) * 120 + 50).astype(
        np.uint8
    )
    observation = MacroblockingAnalyzer(_config()).analyze(
        frame,
        offset_seconds=0.0,
    )
    assert not isinstance(observation, tuple)
    assert observation.valid is True
    assert observation.affected_area_ratio > 0
    assert observation.blocking_confidence > 0
    assert observation.boundary_support_ratio >= 0.5


def test_single_natural_edge_does_not_form_supported_grid():
    frame = np.zeros((96, 128), dtype=np.uint8)
    frame[:, 64:] = 180
    observation = MacroblockingAnalyzer(_config()).analyze(
        frame,
        offset_seconds=0.0,
    )
    assert not isinstance(observation, tuple)
    assert observation.affected_area_ratio == 0.0

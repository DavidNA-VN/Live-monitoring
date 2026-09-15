from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from detectors.macroblocking.difference_maps import build_difference_maps
from detectors.macroblocking.grid_score import score_local_window
from detectors.macroblocking.regions import filter_regions
from detectors.macroblocking.scale_fusion import fuse_scale_maps
from detectors.macroblocking.window_scales import (
    plan_window_scales,
    window_origins,
)
from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFrameObservation,
    MacroblockingScaleEvidence,
)


@dataclass(frozen=True)
class MacroblockingAnalysisDebug:
    heatmap: NDArray[np.float32]
    scale_maps: tuple[NDArray[np.float32], ...]


class MacroblockingAnalyzer:
    def __init__(self, config: MacroblockingAnalyzerConfig) -> None:
        self.config = config

    def analyze(
        self,
        frame: NDArray[np.generic],
        *,
        offset_seconds: float,
        include_debug: bool = False,
    ) -> MacroblockingFrameObservation | tuple[
        MacroblockingFrameObservation,
        MacroblockingAnalysisDebug,
    ]:
        differences = build_difference_maps(frame)
        if differences.frame_std < self.config.minimum_frame_std:
            observation = MacroblockingFrameObservation(
                offset_seconds=offset_seconds,
                affected_area_ratio=0.0,
                blocking_confidence=0.0,
                boundary_support_ratio=0.0,
                valid=False,
                invalid_reason="insufficient_luminance_variation",
            )
            if include_debug:
                empty = np.zeros(frame.shape, dtype=np.float32)
                return observation, MacroblockingAnalysisDebug(empty, ())
            return observation

        height, width = frame.shape
        scale_maps: list[NDArray[np.float32]] = []
        scale_evidence: list[MacroblockingScaleEvidence] = []
        support_sum = np.zeros((height, width), dtype=np.float32)
        support_weight = np.zeros((height, width), dtype=np.float32)

        for scale in plan_window_scales(width, height, self.config):
            scale_map = np.zeros((height, width), dtype=np.float32)
            projection = _window_projection(scale.size)
            confidences: list[float] = []
            supports: list[float] = []
            for y in window_origins(height, scale.size, scale.stride):
                for x in window_origins(width, scale.size, scale.stride):
                    score = score_local_window(
                        differences.horizontal[
                            y : y + scale.size,
                            x : x + scale.size - 1,
                        ],
                        differences.vertical[
                            y : y + scale.size - 1,
                            x : x + scale.size,
                        ],
                        minimum_period=self.config.minimum_period,
                        maximum_period=min(
                            self.config.maximum_period,
                            max(
                                self.config.minimum_period,
                                scale.size // 2,
                            ),
                        ),
                        candidate_periods=self.config.candidate_grid_periods,
                    )
                    confidence = (
                        score.confidence
                        if score.support_ratio
                        >= self.config.minimum_support_ratio
                        else 0.0
                    )
                    window = np.s_[
                        y : y + scale.size,
                        x : x + scale.size,
                    ]
                    np.maximum(
                        scale_map[window],
                        confidence * projection,
                        out=scale_map[window],
                    )
                    if confidence > 0.0:
                        support_sum[y : y + scale.size, x : x + scale.size] += (
                            score.support_ratio
                        )
                        support_weight[
                            y : y + scale.size,
                            x : x + scale.size,
                        ] += 1.0
                    confidences.append(confidence)
                    supports.append(score.support_ratio)
            scale_maps.append(scale_map)
            scale_evidence.append(
                MacroblockingScaleEvidence(
                    window_size=scale.size,
                    blocking_confidence=max(confidences, default=0.0),
                    boundary_support_ratio=max(supports, default=0.0),
                )
            )

        heatmap = fuse_scale_maps(
            tuple(scale_maps),
            strategy=self.config.fusion_strategy,
            support_threshold=self.config.local_confidence_threshold,
        )
        raw_mask = heatmap >= self.config.heatmap_threshold
        minimum_area = max(
            1,
            round(height * width * self.config.minimum_region_area_ratio),
        )
        retained, region_count = filter_regions(
            raw_mask,
            minimum_area=minimum_area,
            fill_minimum_bbox_area=max(
                1,
                round(
                    height
                    * width
                    * self.config.region_fill_minimum_bbox_ratio
                ),
            ),
            fill_minimum_density=self.config.region_fill_minimum_density,
        )
        affected_area_ratio = float(np.mean(retained))
        evidence_mask = retained & raw_mask
        confidence = (
            float(np.mean(heatmap[evidence_mask]))
            if evidence_mask.any()
            else 0.0
        )
        support_map = np.divide(
            support_sum,
            support_weight,
            out=np.zeros_like(support_sum),
            where=support_weight > 0,
        )
        support = (
            float(np.mean(support_map[evidence_mask]))
            if evidence_mask.any()
            else 0.0
        )
        observation = MacroblockingFrameObservation(
            offset_seconds=offset_seconds,
            affected_area_ratio=affected_area_ratio,
            blocking_confidence=confidence,
            boundary_support_ratio=support,
            region_count=region_count,
            scale_evidence=tuple(scale_evidence),
        )
        if include_debug:
            return observation, MacroblockingAnalysisDebug(
                heatmap=heatmap,
                scale_maps=tuple(scale_maps),
            )
        return observation


def _window_projection(size: int) -> NDArray[np.float32]:
    axis = np.linspace(-1.0, 1.0, size, dtype=np.float32)
    weight = 1.0 - 0.20 * np.abs(axis)
    return np.minimum(weight[:, None], weight[None, :]).astype(
        np.float32,
        copy=False,
    )

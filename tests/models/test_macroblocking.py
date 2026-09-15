import math

import pytest

from models.macroblocking import (
    MacroblockingAnalyzerConfig,
    MacroblockingFrameObservation,
    MacroblockingFusionStrategy,
    MacroblockingInterval,
    MacroblockingScaleEvidence,
)


def test_frame_observation_keeps_support_as_first_class_evidence():
    scale = MacroblockingScaleEvidence(64, 0.82, 6 / 7)
    observation = MacroblockingFrameObservation(
        offset_seconds=1.2,
        affected_area_ratio=0.18,
        blocking_confidence=0.80,
        boundary_support_ratio=0.84,
        region_count=2,
        scale_evidence=(scale,),
    )
    assert observation.boundary_support_ratio == pytest.approx(0.84)
    assert observation.scale_evidence[0].boundary_support_ratio == pytest.approx(
        6 / 7
    )


def test_invalid_observation_requires_reason_and_is_not_silently_healthy():
    with pytest.raises(ValueError, match="requires invalid_reason"):
        MacroblockingFrameObservation(0.0, 0.0, 0.0, 0.0, valid=False)

    observation = MacroblockingFrameObservation(
        0.0,
        0.0,
        0.0,
        0.0,
        valid=False,
        invalid_reason="insufficient_texture",
    )
    assert not observation.valid


@pytest.mark.parametrize("value", [-0.01, 1.01, math.nan, math.inf])
def test_unit_interval_fields_are_strict(value):
    with pytest.raises(ValueError):
        MacroblockingFrameObservation(0.0, value, 0.5, 0.5)


def test_interval_rejects_peak_below_average():
    with pytest.raises(ValueError, match="peak affected area"):
        MacroblockingInterval(0.0, 1.0, 0.4, 0.3, 0.7, 0.8, 0.9)


def test_analyzer_config_requires_explicit_replaceable_fusion_strategy():
    with pytest.raises(TypeError, match="explicitly selected"):
        MacroblockingAnalyzerConfig(fusion_strategy="max")

    config = MacroblockingAnalyzerConfig(
        fusion_strategy=MacroblockingFusionStrategy.TOP_TWO_WEIGHTED
    )
    assert config.fusion_strategy is MacroblockingFusionStrategy.TOP_TWO_WEIGHTED
    assert config.relative_scale_divisors == (15, 8, 4)
    assert config.candidate_grid_periods == (4, 6, 8, 12, 16)
    assert not hasattr(config, "affected_area_threshold")
    assert not hasattr(config, "alert_duration")

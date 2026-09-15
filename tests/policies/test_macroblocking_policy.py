import math

import pytest

from models.macroblocking import MacroblockingFrameObservation
from policies.macroblocking import (
    MacroblockingAlertPolicy,
    MacroblockingCandidateRule,
)


def observation(*, area=0.15, confidence=0.70, valid=True):
    return MacroblockingFrameObservation(
        offset_seconds=0.0,
        affected_area_ratio=area,
        blocking_confidence=confidence,
        boundary_support_ratio=0.8,
        valid=valid,
        invalid_reason=None if valid else "decode_gap",
    )


def test_business_area_and_duration_thresholds_are_inclusive():
    policy = MacroblockingAlertPolicy()
    assert not policy.is_affected_area(0.149)
    assert policy.is_affected_area(0.15)
    assert not policy.should_alert(9.999)
    assert policy.should_alert(10.0)


def test_candidate_rule_keeps_detector_and_business_thresholds_distinct():
    rule = MacroblockingCandidateRule(detector_confidence_threshold=0.70)
    assert rule.is_candidate(observation(area=0.15, confidence=0.70))
    assert not rule.is_candidate(observation(area=0.149, confidence=0.99))
    assert not rule.is_candidate(observation(area=0.90, confidence=0.69))
    assert not rule.is_candidate(observation(valid=False))


def test_business_area_can_change_without_changing_detector_threshold():
    rule = MacroblockingCandidateRule(
        alert_policy=MacroblockingAlertPolicy(affected_area_threshold=0.20),
        detector_confidence_threshold=0.70,
    )
    assert not rule.is_candidate(observation(area=0.19, confidence=0.90))
    assert rule.is_candidate(observation(area=0.20, confidence=0.90))
    assert rule.detector_confidence_threshold == 0.70


def test_recovery_requires_one_full_healthy_segment_by_default():
    policy = MacroblockingAlertPolicy()
    assert not policy.recovery_confirmed(0)
    assert policy.recovery_confirmed(1)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("affected_area_threshold", 0.0),
        ("affected_area_threshold", 1.1),
        ("alert_duration", 0.0),
        ("maximum_negative_gap", -0.1),
        ("recovery_healthy_segments", True),
        ("alert_duration", math.nan),
    ],
)
def test_invalid_policy_configuration_is_rejected(name, value):
    with pytest.raises(ValueError):
        MacroblockingAlertPolicy(**{name: value})

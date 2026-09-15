import math

import pytest

from models.freeze import VideoFreezeSeverity
from policies.video_freeze import VideoFreezeAlertPolicy


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(0.0, None), (59.999, None), (60.0, VideoFreezeSeverity.ALERT),
     (120.0, VideoFreezeSeverity.ALERT)],
)
def test_continuous_threshold_is_60_seconds_and_inclusive(duration, expected):
    assert VideoFreezeAlertPolicy().classify(duration) is expected


@pytest.mark.parametrize(
    ("duration", "reference", "expected"),
    [(3.999, 4.0, False), (4.0, 4.0, True), (59.999, 4.0, True),
     (60.0, 4.0, False), (70.0, 4.0, False)],
)
def test_repeated_candidate_uses_reference_segment_duration(
    duration, reference, expected
):
    assert VideoFreezeAlertPolicy().is_repeated_candidate(
        duration, reference
    ) is expected


def test_reference_segment_duration_is_required():
    with pytest.raises(ValueError, match="reference_segment_duration"):
        VideoFreezeAlertPolicy().is_repeated_candidate(4.0)


def test_continuous_notification_is_exactly_once():
    policy = VideoFreezeAlertPolicy()
    assert policy.pending_notification(
        duration=59.999, warning_sent=False, alert_sent=False
    ) is None
    assert policy.pending_notification(
        duration=60.0, warning_sent=False, alert_sent=False
    ) is VideoFreezeSeverity.ALERT
    assert policy.pending_notification(
        duration=70.0, warning_sent=False, alert_sent=True
    ) is None


def test_repeated_and_recovery_thresholds_are_inclusive():
    policy = VideoFreezeAlertPolicy(
        repeated_event_count=3, recovery_healthy_segments=1
    )
    assert not policy.is_repeated_freeze(2)
    assert policy.is_repeated_freeze(3)
    assert not policy.recovery_confirmed(0)
    assert policy.recovery_confirmed(1)


@pytest.mark.parametrize(
    ("name", "value"),
    [("direct_alert_duration", 0.0), ("direct_alert_duration", math.nan),
     ("repeated_window", math.inf), ("repeated_event_count", 0),
     ("repeated_update_every", True), ("recovery_healthy_segments", -1)],
)
def test_invalid_policy_configuration_is_rejected(name, value):
    with pytest.raises(ValueError):
        VideoFreezeAlertPolicy(**{name: value})


@pytest.mark.parametrize("duration", [-1.0, math.nan, math.inf])
def test_invalid_observed_duration_is_rejected(duration):
    with pytest.raises(ValueError, match="duration"):
        VideoFreezeAlertPolicy().classify(duration)

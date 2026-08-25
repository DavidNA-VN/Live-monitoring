import pytest

from policies.audio_loss import AudioLossAlertPolicy


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (29.999, False),
        (30.0, True),
        (30.001, True),
    ],
)
def test_alert_boundary_is_inclusive(duration, expected):
    assert AudioLossAlertPolicy().should_alert(duration) is expected


@pytest.mark.parametrize("duration", [0.0, -1.0, float("inf"), float("nan")])
def test_policy_rejects_invalid_duration(duration):
    with pytest.raises(ValueError, match="alert_duration"):
        AudioLossAlertPolicy(alert_duration=duration)

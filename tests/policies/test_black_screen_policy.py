import pytest

from policies.black_screen import BlackScreenAlertPolicy


def test_default_policy_thresholds():
    policy = BlackScreenAlertPolicy()
    assert policy.direct_alert_duration == 60.0
    assert policy.recovery_healthy_segments == 1
    assert policy.repeated_event_count == 3
    assert policy.repeated_window == 120.0


@pytest.mark.parametrize(
    ("duration", "direct"),
    [
        (0.0, False),
        (59.999, False),
        (60.0, True),
        (65.0, True),
    ],
)
def test_duration_classification_default_60s(
    duration,
    direct,
):
    policy = BlackScreenAlertPolicy()

    assert policy.should_alert_directly(duration) is direct


def test_repeated_candidate_requires_reference_segment_duration():
    assert not BlackScreenAlertPolicy().is_repeated_candidate(10.0)


@pytest.mark.parametrize(
    ("duration", "reference_segment_duration", "is_candidate"),
    [
        (3.999, 4.0, False),  # Dưới reference segment: không vào repeated
        (4.000, 4.0, True),   # Bằng reference segment: là candidate
        (10.0, 4.0, True),    # Giữa reference segment và 60s: là candidate
        (59.999, 4.0, True),  # Cận trên 59.999s: là candidate
        (60.000, 4.0, False), # 60s trở lên: continuous, không vào repeated
    ],
)
def test_repeated_candidate_with_reference_segment_duration(
    duration,
    reference_segment_duration,
    is_candidate,
):
    policy = BlackScreenAlertPolicy(direct_alert_duration=60.0)
    assert (
        policy.is_repeated_candidate(
            duration,
            reference_segment_duration=reference_segment_duration,
        )
        is is_candidate
    )


def test_repeated_threshold_is_inclusive():
    policy = BlackScreenAlertPolicy(
        repeated_event_count=3
    )

    assert policy.is_repeated_black(2) is False
    assert policy.is_repeated_black(3) is True


def test_invalid_policy_parameters_raise():
    with pytest.raises(ValueError, match="recovery_healthy_segments"):
        BlackScreenAlertPolicy(recovery_healthy_segments=0)

    with pytest.raises(ValueError, match="direct_alert_duration"):
        BlackScreenAlertPolicy(direct_alert_duration=0)

    with pytest.raises(ValueError, match="direct_alert_duration"):
        BlackScreenAlertPolicy(direct_alert_duration=float("nan"))

    policy = BlackScreenAlertPolicy()
    assert not policy.should_alert_directly(float("inf"))
    assert not policy.is_repeated_candidate(
        10.0, reference_segment_duration=float("nan")
    )

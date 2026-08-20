import pytest

from policies.black_screen import BlackScreenAlertPolicy


@pytest.mark.parametrize(
    ("duration", "direct", "repeated_candidate"),
    [
        (0.0, False, False),
        (2.999, False, True),
        (3.0, True, False),
        (10.0, True, False),
    ],
)
def test_duration_classification(
    duration,
    direct,
    repeated_candidate,
):
    policy = BlackScreenAlertPolicy(
        direct_alert_duration=3.0
    )

    assert policy.should_alert_directly(duration) is direct
    assert (
        policy.is_repeated_candidate(duration)
        is repeated_candidate
    )


def test_repeated_threshold_is_inclusive():
    policy = BlackScreenAlertPolicy(
        repeated_event_count=3
    )

    assert policy.is_repeated_black(2) is False
    assert policy.is_repeated_black(3) is True

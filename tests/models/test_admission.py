import pytest

from models.admission import LiveAdmissionPolicy, StartupAdmissionMode


def test_admission_policy_has_production_safe_defaults() -> None:
    policy = LiveAdmissionPolicy()

    assert policy.startup_mode is StartupAdmissionMode.BOUNDED_HISTORY
    assert policy.startup_lookback_segments == 4


def test_admission_policy_normalizes_mode_string() -> None:
    policy = LiveAdmissionPolicy(startup_mode="full_snapshot")

    assert policy.startup_mode is StartupAdmissionMode.FULL_SNAPSHOT


@pytest.mark.parametrize("lookback", [0, -1])
def test_admission_policy_rejects_non_positive_lookback(lookback: int) -> None:
    with pytest.raises(ValueError, match="startup_lookback_segments"):
        LiveAdmissionPolicy(startup_lookback_segments=lookback)

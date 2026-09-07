import pytest

from models.admission import AdmissionMode, LiveAdmissionPolicy, StartupAdmissionMode


def test_admission_policy_has_production_safe_defaults() -> None:
    policy = LiveAdmissionPolicy()

    assert policy.startup_mode is StartupAdmissionMode.BOUNDED_HISTORY
    assert policy.startup_lookback_segments == 4
    assert policy.soft_lag_target_durations == 2.0
    assert policy.recovery_lag_target_durations == 1.5
    assert policy.hard_lag_target_durations == 6.0
    assert policy.live_edge_retention_segments == 2
    assert policy.transition_cycles == 3


def test_admission_policy_normalizes_mode_string() -> None:
    policy = LiveAdmissionPolicy(startup_mode="full_snapshot")

    assert policy.startup_mode is StartupAdmissionMode.FULL_SNAPSHOT


@pytest.mark.parametrize("lookback", [0, -1])
def test_admission_policy_rejects_non_positive_lookback(lookback: int) -> None:
    with pytest.raises(ValueError, match="startup_lookback_segments"):
        LiveAdmissionPolicy(startup_lookback_segments=lookback)


def test_admission_modes_are_stable_contract_values() -> None:
    assert AdmissionMode.COVERAGE.value == "coverage"
    assert AdmissionMode.CATCH_UP.value == "catch_up"
    assert AdmissionMode.LIVE_EDGE_PROTECTION.value == "live_edge_protection"


def test_admission_policy_rejects_invalid_hysteresis() -> None:
    with pytest.raises(ValueError, match="must be less"):
        LiveAdmissionPolicy(
            soft_lag_target_durations=2.0,
            recovery_lag_target_durations=2.0,
        )
    with pytest.raises(ValueError, match="must be greater"):
        LiveAdmissionPolicy(
            soft_lag_target_durations=2.0,
            hard_lag_target_durations=2.0,
        )

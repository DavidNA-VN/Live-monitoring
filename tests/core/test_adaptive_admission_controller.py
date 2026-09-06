from core.segment_admission import AdaptiveAdmissionController
from models.admission import AdmissionMode, LiveAdmissionPolicy


def _controller() -> AdaptiveAdmissionController:
    return AdaptiveAdmissionController(
        LiveAdmissionPolicy(
            soft_lag_target_durations=2.0,
            recovery_lag_target_durations=1.0,
            transition_cycles=3,
        )
    )


def test_enters_catch_up_only_after_consecutive_soft_limit_breaches() -> None:
    controller = _controller()

    assert controller.observe(queue_lag_seconds=5.0, target_duration=2.0) is None
    assert controller.observe(queue_lag_seconds=5.0, target_duration=2.0) is None
    transition = controller.observe(
        queue_lag_seconds=5.0,
        target_duration=2.0,
    )

    assert transition is not None
    assert transition.previous is AdmissionMode.COVERAGE
    assert transition.current is AdmissionMode.CATCH_UP
    assert controller.mode is AdmissionMode.CATCH_UP


def test_hysteresis_prevents_mode_flapping_and_recovers_consecutively() -> None:
    controller = _controller()
    for _ in range(3):
        controller.observe(queue_lag_seconds=5.0, target_duration=2.0)

    assert controller.mode is AdmissionMode.CATCH_UP
    assert controller.observe(queue_lag_seconds=3.0, target_duration=2.0) is None
    assert controller.observe(queue_lag_seconds=1.0, target_duration=2.0) is None
    assert controller.observe(queue_lag_seconds=3.0, target_duration=2.0) is None
    assert controller.observe(queue_lag_seconds=1.0, target_duration=2.0) is None
    assert controller.observe(queue_lag_seconds=1.0, target_duration=2.0) is None
    transition = controller.observe(
        queue_lag_seconds=1.0,
        target_duration=2.0,
    )

    assert transition is not None
    assert transition.current is AdmissionMode.COVERAGE


def test_missing_target_duration_does_not_change_mode() -> None:
    controller = _controller()

    for _ in range(5):
        assert (
            controller.observe(queue_lag_seconds=100.0, target_duration=None)
            is None
        )

    assert controller.mode is AdmissionMode.COVERAGE

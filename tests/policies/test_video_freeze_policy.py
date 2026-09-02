import pytest

from policies.video_freeze import (
    VideoFreezeAlertPolicy,
    VideoFreezeSeverity,
)


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (0.0, None),
        (2.999, None),
        (3.0, VideoFreezeSeverity.WARNING),
        (4.999, VideoFreezeSeverity.WARNING),
        (5.0, VideoFreezeSeverity.ALERT),
        (20.0, VideoFreezeSeverity.ALERT),
    ],
)
def test_duration_classification(duration, expected):
    assert VideoFreezeAlertPolicy().classify(duration) is expected


@pytest.mark.parametrize("duration", [3.0, 4.0, 4.999])
def test_warning_events_are_repeated_candidates(duration):
    assert VideoFreezeAlertPolicy().is_repeated_candidate(duration)


@pytest.mark.parametrize("duration", [0.0, 2.999, 5.0, 10.0])
def test_non_warning_events_are_not_repeated_candidates(duration):
    assert not VideoFreezeAlertPolicy().is_repeated_candidate(duration)


def test_repeated_threshold_is_inclusive():
    policy = VideoFreezeAlertPolicy(repeated_event_count=3)

    assert not policy.is_repeated_freeze(2)
    assert policy.is_repeated_freeze(3)


def test_notification_escalates_without_duplicate_messages():
    policy = VideoFreezeAlertPolicy()

    assert policy.pending_notification(
        duration=2.999,
        warning_sent=False,
        alert_sent=False,
    ) is None
    assert policy.pending_notification(
        duration=3.0,
        warning_sent=False,
        alert_sent=False,
    ) is VideoFreezeSeverity.WARNING
    assert policy.pending_notification(
        duration=4.0,
        warning_sent=True,
        alert_sent=False,
    ) is None
    assert policy.pending_notification(
        duration=5.0,
        warning_sent=True,
        alert_sent=False,
    ) is VideoFreezeSeverity.ALERT
    assert policy.pending_notification(
        duration=6.0,
        warning_sent=True,
        alert_sent=True,
    ) is None


def test_first_observation_above_alert_threshold_skips_stale_warning():
    assert VideoFreezeAlertPolicy().pending_notification(
        duration=6.0,
        warning_sent=False,
        alert_sent=False,
    ) is VideoFreezeSeverity.ALERT


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("warning_duration", 0.0),
        ("warning_duration", float("nan")),
        ("alert_duration", float("inf")),
        ("repeated_window", -1.0),
    ],
)
def test_policy_rejects_invalid_duration_configuration(field, value):
    with pytest.raises(ValueError, match=field):
        VideoFreezeAlertPolicy(**{field: value})


def test_alert_threshold_must_be_greater_than_warning_threshold():
    with pytest.raises(ValueError, match="greater than warning_duration"):
        VideoFreezeAlertPolicy(warning_duration=5.0, alert_duration=5.0)


@pytest.mark.parametrize("count", [0, -1, 1.5, True])
def test_policy_rejects_invalid_repeated_count(count):
    with pytest.raises(ValueError, match="repeated_event_count"):
        VideoFreezeAlertPolicy(repeated_event_count=count)


@pytest.mark.parametrize("duration", [-0.001, float("nan"), float("inf")])
def test_classification_rejects_invalid_observed_duration(duration):
    with pytest.raises(ValueError, match="duration"):
        VideoFreezeAlertPolicy().classify(duration)


@pytest.mark.parametrize("count", [-1, 1.5, True])
def test_repeated_check_rejects_invalid_observed_count(count):
    with pytest.raises(ValueError, match="event_count"):
        VideoFreezeAlertPolicy().is_repeated_freeze(count)

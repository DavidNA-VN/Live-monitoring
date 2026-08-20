import pytest

from checks.black_screen.repeated_reducer import (
    RepeatedAlertState,
    RepeatedBlackReducer,
    RepeatedBlackState,
    ShortBlackRecord,
)
from policies.black_screen import BlackScreenAlertPolicy


def record(event_id, event_at, duration=1.0):
    return ShortBlackRecord(
        event_id=event_id,
        event_at=event_at,
        duration=duration,
    )


def reducer():
    return RepeatedBlackReducer(
        BlackScreenAlertPolicy(
            repeated_event_count=3,
            repeated_window=120.0,
            repeated_update_every=3,
            repeated_recovery_window=120.0,
        )
    )


def apply_records(*records):
    instance = reducer()
    reduction = None
    state = RepeatedBlackState()
    for item in records:
        reduction = instance.record_short_event(
            state=state,
            record=item,
        )
        state = reduction.state
    return instance, reduction


def test_third_short_event_opens_repeated_incident():
    _, reduction = apply_records(
        record("event-1", 10.0, 0.5),
        record("event-2", 20.0, 1.0),
        record("event-3", 30.0, 1.5),
    )

    incident = reduction.state.incident
    assert reduction.alert.state == RepeatedAlertState.OPEN
    assert incident.incident_id == "event-3"
    assert incident.first_event_id == "event-1"
    assert incident.latest_event_id == "event-3"
    assert incident.occurrences == 3
    assert incident.total_black_duration == pytest.approx(3.0)
    assert incident.last_notified_occurrences == 3


def test_existing_incident_only_updates_alert_at_configured_step():
    instance, opened = apply_records(
        record("event-1", 10.0),
        record("event-2", 20.0),
        record("event-3", 30.0),
    )
    state = opened.state

    for number in (4, 5):
        reduction = instance.record_short_event(
            state=state,
            record=record(f"event-{number}", number * 10.0),
        )
        state = reduction.state
        assert reduction.alert is None

    reduction = instance.record_short_event(
        state=state,
        record=record("event-6", 60.0),
    )

    assert reduction.alert.state == RepeatedAlertState.UPDATE
    assert reduction.alert.occurrences == 6
    assert reduction.state.incident.last_notified_occurrences == 6


def test_history_at_window_boundary_is_pruned():
    instance = reducer()
    state = RepeatedBlackState(
        history=(record("stale", 10.0),)
    )

    reduction = instance.record_short_event(
        state=state,
        record=record("current", 130.0),
    )

    assert [
        item.event_id for item in reduction.state.history
    ] == ["current"]


def test_duplicate_event_replaces_history_record():
    instance = reducer()
    state = RepeatedBlackState(
        history=(record("same", 10.0, 1.0),)
    )

    reduction = instance.record_short_event(
        state=state,
        record=record("same", 20.0, 2.0),
    )

    assert reduction.state.history == (
        record("same", 20.0, 2.0),
    )


def test_incident_resolves_at_inclusive_quiet_boundary():
    instance, opened = apply_records(
        record("event-1", 10.0),
        record("event-2", 20.0),
        record("event-3", 30.0),
    )

    before = instance.resolve_if_quiet(
        state=opened.state,
        reference_time=149.999,
    )
    resolved = instance.resolve_if_quiet(
        state=opened.state,
        reference_time=150.0,
    )

    assert before.alert is None
    assert resolved.alert.state == RepeatedAlertState.RESOLVED
    assert resolved.alert.event_id == "event-3"
    assert resolved.clear_state is True
    assert resolved.state == RepeatedBlackState()

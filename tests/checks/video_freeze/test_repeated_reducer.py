from checks.video_freeze.repeated_reducer import RepeatedFreezeReducer
from checks.video_freeze.repeated_state import (
    FreezeWarningRecord,
    RepeatedFreezeState,
)
from policies.video_freeze import VideoFreezeAlertPolicy


def record(event_id, event_at, duration=4.0):
    return FreezeWarningRecord(event_id, event_at, duration)


def evidenced_record(event_id, event_at, sequence):
    return FreezeWarningRecord(
        event_id=event_id,
        event_at=event_at,
        duration=4.0,
        start_sequence=sequence,
        end_sequence=sequence,
        start_segment_uri=f"https://origin/{sequence}.ts",
        end_segment_uri=f"https://origin/{sequence}.ts",
        affected_segment_count=1,
    )


def test_third_candidate_opens_repeated_incident():
    reducer = RepeatedFreezeReducer(VideoFreezeAlertPolicy())
    state = RepeatedFreezeState()
    for item in (record("e1", 10), record("e2", 20), record("e3", 30)):
        reduction = reducer.record_candidate(state=state, record=item)
        state = reduction.state
    assert reduction.alert.state == "OPEN"
    assert reduction.alert.event_id == "e3"
    assert reduction.alert.occurrences == 3


def test_duplicate_candidate_does_not_increment_incident():
    reducer = RepeatedFreezeReducer(VideoFreezeAlertPolicy())
    state = RepeatedFreezeState()
    for item in (record("e1", 10), record("e2", 20), record("e3", 30)):
        state = reducer.record_candidate(state=state, record=item).state
    reduction = reducer.record_candidate(state=state, record=record("e3", 30))
    assert reduction.alert is None
    assert reduction.state.incident.occurrences == 3


def test_repeated_alert_preserves_non_contiguous_segment_evidence():
    reducer = RepeatedFreezeReducer(VideoFreezeAlertPolicy())
    state = RepeatedFreezeState()
    for item in (
        evidenced_record("e1", 10, 100),
        evidenced_record("e2", 20, 105),
        evidenced_record("e3", 30, 110),
    ):
        reduction = reducer.record_candidate(state=state, record=item)
        state = reduction.state

    assert reduction.alert.start_sequence == 100
    assert reduction.alert.end_sequence == 110
    assert reduction.alert.start_segment_uri == "https://origin/100.ts"
    assert reduction.alert.end_segment_uri == "https://origin/110.ts"
    assert reduction.alert.affected_segment_count == 3


def test_recovery_consumes_history_so_e4_starts_a_new_window():
    reducer = RepeatedFreezeReducer(VideoFreezeAlertPolicy())
    state = RepeatedFreezeState()
    for item in (record("e1", 10), record("e2", 20), record("e3", 30)):
        state = reducer.record_candidate(state=state, record=item).state
    recovered = reducer.resolve_incident(state=state)
    assert recovered.alert.state == "RESOLVED"
    assert recovered.clear_state
    assert recovered.state == RepeatedFreezeState()

    next_reduction = reducer.record_candidate(
        state=recovered.state, record=record("e4", 40)
    )
    assert next_reduction.alert is None
    assert [item.event_id for item in next_reduction.state.history] == ["e4"]


def test_rolling_window_prunes_boundary_record():
    reducer = RepeatedFreezeReducer(VideoFreezeAlertPolicy())
    state = RepeatedFreezeState(history=(record("old", 0), record("new", 1)))
    reduction = reducer.record_candidate(
        state=state, record=record("latest", 120)
    )
    assert [item.event_id for item in reduction.state.history] == [
        "new",
        "latest",
    ]

from threading import Event, Lock, Thread
from time import sleep

from core.profile_worker import ProfileWorkerCoordinator
from models.analysis import SegmentAnalysisBundle
from tests.core.test_profile_worker_shutdown import _Profile, _StateStore, _work


def _prepare_coordinator(monkeypatch, *, max_parallel_analysis=2):
    state_store = _StateStore()
    coordinator = ProfileWorkerCoordinator(
        state_store=state_store,
        max_parallel_analysis=max_parallel_analysis,
    )
    monkeypatch.setattr(
        coordinator,
        "_claim_processors",
        lambda item, _blocked: [(item.processors[0].processor, object())],
    )
    monkeypatch.setattr(coordinator, "_start_heartbeats", lambda _claims: [])
    return coordinator, state_store


def test_analysis_can_finish_out_of_order_but_commit_remains_ordered(
    monkeypatch,
):
    coordinator, _state_store = _prepare_coordinator(monkeypatch)
    first_can_finish = Event()
    second_finished = Event()
    commit_order = []

    def analyze(_profile, segment, _claims, _blocked):
        if segment.sequence == 1:
            assert first_can_finish.wait(timeout=2.0)
        else:
            second_finished.set()
        return SegmentAnalysisBundle(profile_name="video_realtime")

    def commit(_processor, segment, _claim, _analysis):
        commit_order.append(segment.sequence)
        return True

    monkeypatch.setattr(coordinator, "_analyze", analyze)
    monkeypatch.setattr(coordinator, "_process_claimed_segment", commit)
    completed = []
    thread = Thread(
        target=coordinator.process_batch,
        args=(_Profile(), [_work(1), _work(2)]),
        kwargs={"on_item_completed": completed.append},
    )
    thread.start()

    assert second_finished.wait(timeout=2.0)
    assert commit_order == []
    first_can_finish.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert commit_order == [1, 2]
    assert [item.sequence for item in completed] == [1, 2]


def test_parallel_analysis_window_is_bounded(monkeypatch):
    coordinator, _state_store = _prepare_coordinator(
        monkeypatch,
        max_parallel_analysis=2,
    )
    lock = Lock()
    active = 0
    peak_active = 0

    def analyze(_profile, _segment, _claims, _blocked):
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        sleep(0.03)
        with lock:
            active -= 1
        return SegmentAnalysisBundle(profile_name="video_realtime")

    monkeypatch.setattr(coordinator, "_analyze", analyze)
    monkeypatch.setattr(
        coordinator,
        "_process_claimed_segment",
        lambda *_args: True,
    )

    coordinator.process_batch(
        _Profile(),
        [_work(1), _work(2), _work(3), _work(4)],
    )

    assert peak_active == 2


def test_parallel_analysis_limit_must_be_positive():
    try:
        ProfileWorkerCoordinator(
            state_store=_StateStore(),
            max_parallel_analysis=0,
        )
    except ValueError as exc:
        assert "max_parallel_analysis" in str(exc)
    else:
        raise AssertionError("Expected non-positive parallel limit to fail")

from core.profile_worker import (
    ProcessorSegmentWork,
    ProfileSegmentWork,
    ProfileWorkerCoordinator,
)
from core.segment_admission import ProfileSegmentIdentity
from models.analysis import AnalysisRequirement, SegmentAnalysisBundle
from models.processing import SegmentProcessingIdentity
from tests.factories.hls import make_segment


class _Processor:
    name = "black_screen"
    requirements = frozenset({AnalysisRequirement.BLACK_INTERVALS})


class _Profile:
    name = "video_realtime"


class _StateStore:
    def __init__(self):
        self.relinquished = []

    def mark_retryable_failure(self, claim, reason):
        raise AssertionError("Ordered deferral must not consume an attempt")

    def relinquish(self, claim, reason):
        self.relinquished.append((claim, reason))


def _work(sequence: int) -> ProfileSegmentWork:
    return ProfileSegmentWork(
        admission_identity=ProfileSegmentIdentity(
            profile_name="video_realtime",
            variant_stable_id="variant-1",
            timeline_generation=0,
            discontinuity_sequence=0,
            sequence=sequence,
            media_revision=f"revision-{sequence}",
        ),
        segment=make_segment(sequence),
        processors=(
            ProcessorSegmentWork(
                processor=_Processor(),
                identity=SegmentProcessingIdentity(
                    storage_id="storage-1",
                    check_name="black_screen",
                    variant_stable_id="variant-1",
                    timeline_generation=0,
                    discontinuity_sequence=0,
                    sequence=sequence,
                    media_revision=f"revision-{sequence}",
                ),
            ),
        ),
    )


def test_stop_request_prevents_batch_from_starting_another_segment(monkeypatch):
    state_store = _StateStore()
    coordinator = ProfileWorkerCoordinator(state_store=state_store)
    processed: list[int] = []

    monkeypatch.setattr(
        coordinator,
        "_claim_processors",
        lambda item, _blocked: [(item.processors[0].processor, object())],
    )
    monkeypatch.setattr(coordinator, "_start_heartbeats", lambda _claims: [])
    monkeypatch.setattr(
        coordinator,
        "_analyze",
        lambda _profile, _segment, _claims, _blocked: SegmentAnalysisBundle(
            profile_name="video_realtime"
        ),
    )

    def process_one(_processor, segment, _claim, _analysis):
        processed.append(segment.sequence)
        coordinator.request_stop()
        return True

    monkeypatch.setattr(coordinator, "_process_claimed_segment", process_one)

    completed = []
    coordinator.process_batch(
        _Profile(),
        [_work(1), _work(2), _work(3)],
        on_item_completed=completed.append,
    )

    assert processed == [1]
    assert [item.sequence for item in completed] == [1]
    assert len(state_store.relinquished) == 1
    assert "shutdown" in state_store.relinquished[0][1]


def test_retryable_segment_is_not_reported_as_completed(monkeypatch):
    coordinator = ProfileWorkerCoordinator(state_store=_StateStore())
    item = _work(1)
    monkeypatch.setattr(
        coordinator,
        "_claim_processors",
        lambda current, _blocked: [
            (current.processors[0].processor, object())
        ],
    )
    monkeypatch.setattr(coordinator, "_start_heartbeats", lambda _claims: [])
    monkeypatch.setattr(
        coordinator,
        "_analyze",
        lambda _profile, _segment, _claims, _blocked: SegmentAnalysisBundle(
            profile_name="video_realtime"
        ),
    )
    monkeypatch.setattr(
        coordinator,
        "_process_claimed_segment",
        lambda *_args: False,
    )
    completed = []

    coordinator.process_batch(
        _Profile(),
        [item],
        on_item_completed=completed.append,
    )

    assert completed == []


def test_retryable_segment_does_not_complete_later_work(monkeypatch):
    state_store = _StateStore()
    coordinator = ProfileWorkerCoordinator(state_store=state_store)
    attempted: list[int] = []
    monkeypatch.setattr(
        coordinator,
        "_claim_processors",
        lambda current, blocked: []
        if current.processors[0].processor.name in blocked
        else [(current.processors[0].processor, object())],
    )
    monkeypatch.setattr(coordinator, "_start_heartbeats", lambda _claims: [])
    monkeypatch.setattr(
        coordinator,
        "_analyze",
        lambda _profile, _segment, _claims, _blocked: SegmentAnalysisBundle(
            profile_name="video_realtime"
        ),
    )

    def retryable_failure(_processor, segment, _claim, _analysis):
        attempted.append(segment.sequence)
        return False

    monkeypatch.setattr(
        coordinator,
        "_process_claimed_segment",
        retryable_failure,
    )
    completed = []

    coordinator.process_batch(
        _Profile(),
        [_work(1), _work(2)],
        on_item_completed=completed.append,
    )

    assert attempted == [1]
    assert completed == []
    assert len(state_store.relinquished) == 1
    assert "earlier segment" in state_store.relinquished[0][1]


def test_completion_callback_failure_does_not_abort_batch(monkeypatch):
    coordinator = ProfileWorkerCoordinator(state_store=_StateStore())
    processed: list[int] = []
    monkeypatch.setattr(
        coordinator,
        "_claim_processors",
        lambda current, _blocked: [
            (current.processors[0].processor, object())
        ],
    )
    monkeypatch.setattr(coordinator, "_start_heartbeats", lambda _claims: [])
    monkeypatch.setattr(
        coordinator,
        "_analyze",
        lambda _profile, _segment, _claims, _blocked: SegmentAnalysisBundle(
            profile_name="video_realtime"
        ),
    )
    monkeypatch.setattr(
        coordinator,
        "_process_claimed_segment",
        lambda _processor, segment, _claim, _analysis: (
            processed.append(segment.sequence) or True
        ),
    )

    def fail_acknowledgement(_identity):
        raise RuntimeError("admission queue unavailable")

    coordinator.process_batch(
        _Profile(),
        [_work(1), _work(2)],
        on_item_completed=fail_acknowledgement,
    )

    assert processed == [1, 2]

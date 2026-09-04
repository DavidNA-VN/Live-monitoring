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
    coordinator = ProfileWorkerCoordinator(state_store=object())
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

    coordinator.process_batch(_Profile(), [_work(1), _work(2), _work(3)])

    assert processed == [1]

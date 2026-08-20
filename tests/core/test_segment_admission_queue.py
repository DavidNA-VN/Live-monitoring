from core.segment_admission import (
    AdmissionDropReason,
    SegmentAdmissionQueue,
)
from tests.factories.hls import make_segment


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_refresh_keeps_original_deadline_and_updates_descriptor():
    clock = FakeClock()
    queue = SegmentAdmissionQueue(
        max_items=10,
        max_age_seconds=30,
        clock=clock,
    )
    original = make_segment(100)
    refreshed = make_segment(100)
    refreshed.uri = "https://media.test/refreshed-token.ts"

    first = queue.admit(
        profile_name="video_realtime",
        segments=[original],
    )
    clock.advance(20)
    second = queue.admit(
        profile_name="video_realtime",
        segments=[refreshed],
    )

    assert first.admitted == 1
    assert second.refreshed == 1
    assert queue.snapshot()[0].segment.uri == refreshed.uri

    clock.advance(10)
    drops = queue.expire()

    assert [drop.reason for drop in drops] == [
        AdmissionDropReason.EXPIRED
    ]


def test_capacity_drops_oldest_pending_item_explicitly():
    queue = SegmentAdmissionQueue(
        max_items=2,
        max_age_seconds=30,
        clock=FakeClock(),
    )

    result = queue.admit(
        profile_name="video_realtime",
        segments=[
            make_segment(100),
            make_segment(101),
            make_segment(102),
        ],
    )

    assert queue.depth == 2
    assert [item.segment.sequence for item in queue.snapshot()] == [
        101,
        102,
    ]
    assert result.drops[0].identity.sequence == 100
    assert result.drops[0].reason == AdmissionDropReason.CAPACITY


def test_inflight_item_is_not_evicted_by_capacity():
    queue = SegmentAdmissionQueue(
        max_items=1,
        max_age_seconds=30,
        clock=FakeClock(),
    )
    queue.admit(
        profile_name="video_realtime",
        segments=[make_segment(100)],
    )
    identity = queue.snapshot()[0].identity
    queue.protect([identity])

    result = queue.admit(
        profile_name="video_realtime",
        segments=[make_segment(101)],
    )

    assert [item.segment.sequence for item in queue.snapshot()] == [100]
    assert result.drops[0].identity.sequence == 101


def test_acknowledged_item_is_suppressed_while_playlist_retains_it():
    clock = FakeClock()
    queue = SegmentAdmissionQueue(
        max_items=10,
        max_age_seconds=30,
        clock=clock,
    )
    segment = make_segment(100)
    queue.admit(
        profile_name="video_realtime",
        segments=[segment],
    )
    queue.acknowledge([queue.snapshot()[0].identity])

    result = queue.admit(
        profile_name="video_realtime",
        segments=[segment],
    )

    assert result.suppressed == 1
    assert queue.depth == 0

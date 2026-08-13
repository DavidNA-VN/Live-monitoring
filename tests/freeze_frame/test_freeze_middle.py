from checks.freeze_frame.check import FreezeFrameCheck
from core.context import build_monitoring_context


MASTER_URL = (
    "http://127.0.0.1:8000/"
    "freeze_15s_sample/master.m3u8"
)


def test_freeze_middle_detected():

    context = build_monitoring_context(
        MASTER_URL
    )
    results = FreezeFrameCheck().run_raw(context)

    assert len(results) == 2

    for variant_result in results.values():

        events = variant_result.events

        assert len(events) == 1

        event = events[0]

        # Freeze dự kiến khoảng 4s -> 11s
        assert 3.8 <= event.start_time <= 4.1

        assert 10.9 <= event.end_time <= 11.2

        assert 6.9 <= event.duration <= 7.3

        assert event.start_sequence == 1
        assert event.end_sequence == 5

        assert event.affected_segments == [
            1,
            2,
            3,
            4,
            5,
        ]

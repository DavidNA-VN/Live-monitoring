from checks.freeze_frame.check import FreezeFrameCheck
from core.context import build_monitoring_context


MASTER_URL = (
    "http://127.0.0.1:8000/"
    "freeze_to_eof/master.m3u8"
)


def test_freeze_to_eof_detected():

    context = build_monitoring_context(
        MASTER_URL
    )
    results = FreezeFrameCheck().run_raw(context)

    assert len(results) == 2

    for variant_result in results.values():

        events = variant_result.events

        assert len(events) == 1

        event = events[0]

        # Freeze dự kiến khoảng 8s -> EOF 15s
        assert 7.8 <= event.start_time <= 8.1

        assert 14.9 <= event.end_time <= 15.1

        assert 6.9 <= event.duration <= 7.3

        assert event.start_sequence == 3
        assert event.end_sequence == 7

        assert event.affected_segments == [
            3,
            4,
            5,
            6,
            7,
        ]

from checks.black_screen.check import BlackScreenCheck
from core.context import build_monitoring_context


MASTER_URL = (
    "http://127.0.0.1:8000/"
    "black_screen_0/master.m3u8"
)


def detect_events(master_url: str):
    context = build_monitoring_context(
        master_url
    )
    results = BlackScreenCheck().run_raw(context)

    return {
        variant_id: variant_result.events
        for variant_id, variant_result in results.items()
    }


def test_black_screen_0_variant_count():
    events = detect_events(MASTER_URL)

    assert len(events) == 2
    assert "720p" in events
    assert "480p" in events


def test_black_screen_0_event_count():
    events = detect_events(MASTER_URL)

    for variant_id, variant_events in events.items():
        assert len(variant_events) == 1, (
            f"{variant_id} expected 1 event, "
            f"got {len(variant_events)}"
        )


def test_black_screen_0_timeline():
    events = detect_events(MASTER_URL)

    for variant_id, variant_events in events.items():
        event = variant_events[0]

        assert abs(event.start_time - 0.167) < 0.1
        assert abs(event.end_time - 7.958) < 0.1

        assert 7.6 < event.duration < 8.0


def test_black_screen_0_segments():
    events = detect_events(MASTER_URL)

    for variant_id, variant_events in events.items():
        event = variant_events[0]

        assert event.affected_segments == [
            0,
            1,
            2,
            3,
        ]

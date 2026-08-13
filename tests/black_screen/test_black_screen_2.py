from checks.black_screen.check import BlackScreenCheck
from core.context import build_monitoring_context


MASTER_URL = (
    "http://127.0.0.1:8000/"
    "black_screen_2/master.m3u8"
)


EXPECTED_EVENTS = [
    (0.20, 0.83),
    (1.10, 1.27),
    (1.90, 2.13),
    (3.90, 4.17),
    (5.00, 6.43),
    (7.90, 8.13),
]


def detect_events(master_url: str):
    context = build_monitoring_context(
        master_url
    )
    results = BlackScreenCheck().run_raw(context)

    return {
        variant_id: variant_result.events
        for variant_id, variant_result in results.items()
    }


def test_black_screen_1_event_count():
    events = detect_events(MASTER_URL)

    for variant_id, variant_events in events.items():
        assert len(variant_events) == 6, (
            f"{variant_id} expected 6 events, "
            f"got {len(variant_events)}"
        )


def test_black_screen_1_timeline():
    events = detect_events(MASTER_URL)

    tolerance = 0.1

    for variant_id, variant_events in events.items():

        for actual, expected in zip(
            variant_events,
            EXPECTED_EVENTS,
        ):
            expected_start, expected_end = expected

            assert (
                abs(
                    actual.start_time
                    - expected_start
                )
                < tolerance
            )

            assert (
                abs(
                    actual.end_time
                    - expected_end
                )
                < tolerance
            )


def test_black_screen_1_cross_segment_events():
    events = detect_events(MASTER_URL)

    expected_segments = [
        [0],
        [0],
        [0, 1],
        [1, 2],
        [2, 3],
        [3, 4],
    ]

    for variant_id, variant_events in events.items():

        actual_segments = [
            event.affected_segments
            for event in variant_events
        ]

        assert actual_segments == expected_segments

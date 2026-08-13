from checks.black_screen.check import BlackScreenCheck
from core.context import build_monitoring_context


MASTER_URL = (
    "http://127.0.0.1:8000/"
    "busquet/master.m3u8"
)


def test_normal_video_has_no_black_event():
    context = build_monitoring_context(
        MASTER_URL
    )
    results = BlackScreenCheck().run_raw(context)

    for variant_id, variant_result in results.items():
        assert variant_result.events == [], (
            f"{variant_id} unexpectedly "
            f"detected black events: {variant_result.events}"
        )

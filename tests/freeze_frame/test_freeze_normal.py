from checks.freeze_frame.check import FreezeFrameCheck
from core.context import build_monitoring_context


MASTER_URL = (
    "http://127.0.0.1:8000/"
    "busquet/master.m3u8"
)


def test_normal_video_has_no_freeze():
    context = build_monitoring_context(
        MASTER_URL
    )
    results = FreezeFrameCheck().run_raw(context)

    assert len(results) == 2

    for variant_result in results.values():
        assert variant_result.events == []

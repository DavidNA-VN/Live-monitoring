from threading import Event, Lock

from core import context
from playlist.master_parser import Variant
from tests.factories.hls import make_snapshot


def test_media_playlists_are_fetched_with_bounded_concurrency(
    monkeypatch,
):
    variants = [
        Variant(
            id=f"variant-{index}",
            stable_id=f"stable-{index}",
            uri=f"https://media.test/{index}.m3u8",
            bandwidth=None,
            resolution=None,
        )
        for index in range(3)
    ]
    release = Event()
    two_started = Event()
    lock = Lock()
    active = 0
    peak = 0

    def load_media(variant, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
        if not release.is_set():
            assert two_started.wait(timeout=1)
            release.set()
        with lock:
            active -= 1
        return make_snapshot(
            [100],
            variant_id=variant.id,
            variant_stable_id=variant.stable_id,
        )

    monkeypatch.setattr(
        context,
        "parse_master_playlist",
        lambda *_args, **_kwargs: variants,
    )
    monkeypatch.setattr(context, "parse_media_playlist", load_media)

    result = context.build_monitoring_context(
        "https://media.test/master.m3u8",
        media_playlist_workers=2,
    )

    assert peak == 2
    assert len(result.snapshots_by_variant) == 3


def test_media_playlist_worker_limit_is_validated():
    try:
        context.build_monitoring_context(
            "https://media.test/master.m3u8",
            media_playlist_workers=0,
        )
    except ValueError as exc:
        assert str(exc) == "media_playlist_workers must be > 0"
    else:
        raise AssertionError("expected worker limit validation")

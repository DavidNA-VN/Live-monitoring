from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Lock
from time import sleep

import pytest

from core.profile_worker import ProfileWorkerCoordinator
from core.profile_scheduler import ProfileScheduler
from core.analysis_profile import AnalysisResourceClass
from models.analysis import (
    AnalysisRequirement,
    ResourcePoolLimit,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
    default_resource_limits,
)
from models.stream import StreamIdentity
from tests.factories.hls import make_segment


class FakeStateStore:
    pass


class FakeProfile:
    name = "video_realtime"
    provides = frozenset(
        {AnalysisRequirement.BLACK_INTERVALS}
    )


class FakeProcessor:
    name = "black_screen"
    analysis_profile = "video_realtime"
    requirements = frozenset(
        {AnalysisRequirement.BLACK_INTERVALS}
    )


def build(*, processors, profiles):
    return ProfileScheduler(
        stream=StreamIdentity(
            stream_id="stream-1",
            master_url="https://example.test/master.m3u8",
        ),
        state_store=FakeStateStore(),
        processors=processors,
        analysis_profiles=profiles,
        resource_limits=default_resource_limits(),
        max_concurrent_media_processes=2,
    )


def test_unknown_profile_fails_fast():
    processor = FakeProcessor()
    processor.analysis_profile = "missing"

    with pytest.raises(ValueError, match="unknown analysis profile"):
        build(processors=[processor], profiles=[FakeProfile()])


def test_missing_requirement_fails_fast():
    profile = FakeProfile()
    profile.provides = frozenset()

    with pytest.raises(ValueError, match="does not provide"):
        build(processors=[FakeProcessor()], profiles=[profile])


def test_duplicate_profile_name_fails_fast():
    with pytest.raises(ValueError, match="must be unique"):
        build(
            processors=[],
            profiles=[FakeProfile(), FakeProfile()],
        )


def test_profiles_of_different_resource_classes_use_separate_pools():
    video = FakeProfile()
    video.resource_class = AnalysisResourceClass.VIDEO_DECODE
    audio = FakeProfile()
    audio.name = "audio_realtime"
    audio.resource_class = AnalysisResourceClass.AUDIO_DECODE

    scheduler = build(processors=[], profiles=[video, audio])
    try:
        assert (
            scheduler.executors_by_resource[
                AnalysisResourceClass.VIDEO_DECODE
            ]
            is not scheduler.executors_by_resource[
                AnalysisResourceClass.AUDIO_DECODE
            ]
        )
        assert scheduler.executors_by_resource[
            AnalysisResourceClass.VIDEO_DECODE
        ].max_workers == 4
        assert scheduler.executors_by_resource[
            AnalysisResourceClass.AUDIO_DECODE
        ].max_workers == 1
    finally:
        scheduler.shutdown()


def test_explicit_resource_limits_do_not_multiply_one_decode_budget():
    video = FakeProfile()
    video.resource_class = AnalysisResourceClass.VIDEO_DECODE
    audio = FakeProfile()
    audio.name = "audio_realtime"
    audio.resource_class = AnalysisResourceClass.AUDIO_DECODE
    limits = default_resource_limits()
    limits[AnalysisResourceClass.VIDEO_DECODE] = ResourcePoolLimit(3, 5)
    limits[AnalysisResourceClass.AUDIO_DECODE] = ResourcePoolLimit(1, 2)

    scheduler = ProfileScheduler(
        stream=StreamIdentity("stream-1", "https://test/master.m3u8"),
        state_store=FakeStateStore(),
        processors=[],
        analysis_profiles=[video, audio],
        resource_limits=limits,
        max_concurrent_media_processes=3,
    )
    try:
        assert scheduler.executors_by_resource[
            AnalysisResourceClass.VIDEO_DECODE
        ].max_workers == 3
        assert scheduler.executors_by_resource[
            AnalysisResourceClass.AUDIO_DECODE
        ].max_workers == 1
    finally:
        scheduler.shutdown()


def test_global_media_process_gate_caps_cross_pool_analysis():
    class ConcurrentProfile:
        resource_class = AnalysisResourceClass.AUDIO_DECODE

        def __init__(self):
            self.lock = Lock()
            self.active = 0
            self.max_active = 0

        def analyze(self, _segment, *, requirements):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            sleep(0.03)
            with self.lock:
                self.active -= 1
            return SegmentAnalysisBundle(
                profile_name="audio_realtime",
                video_realtime=VideoRealtimeAnalysis(checked=True),
            )

    profile = ConcurrentProfile()
    processor = FakeProcessor()
    coordinator = ProfileWorkerCoordinator(
        FakeStateStore(),
        media_process_gate=BoundedSemaphore(2),
    )

    def analyze(index):
        return coordinator._analyze(
            profile,
            make_segment(index),
            [(processor, None)],
            set(),
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(analyze, range(5)))

    assert profile.max_active == 2

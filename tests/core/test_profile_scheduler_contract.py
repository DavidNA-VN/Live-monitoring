from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Lock
from time import sleep

import pytest

from core.profile_worker import ProfileWorkerCoordinator
from core.profile_scheduler import ProfileScheduler
from core.analysis_profile import AnalysisResourceClass
from core.segment_admission import (
    AdmittedProfileSegment,
    ProfileSegmentIdentity,
)
from models.analysis import (
    AnalysisRequirement,
    AudioRealtimeAnalysis,
    ResourcePoolLimit,
    SegmentAnalysisBundle,
    VideoRealtimeAnalysis,
    default_resource_limits,
)
from models.audio import AudioTrackPresence
from models.stream import StreamIdentity, build_stream_identity
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
            external_stream_id="stream-1",
            storage_id="storage-hash-1",
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
        stream=StreamIdentity(
            external_stream_id="stream-1",
            storage_id="storage-hash-1",
            master_url="https://test/master.m3u8",
        ),
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


def test_profile_scheduler_builds_processing_identity_with_storage_id():
    video = FakeProfile()
    processor = FakeProcessor()
    identity = build_stream_identity(
        "https://example/master.m3u8",
        "channel-01",
    )
    scheduler = ProfileScheduler(
        stream=identity,
        state_store=FakeStateStore(),
        processors=[processor],
        analysis_profiles=[video],
        resource_limits=default_resource_limits(),
        max_concurrent_media_processes=1,
    )
    try:
        segment = make_segment(10)
        item = AdmittedProfileSegment(
            identity=ProfileSegmentIdentity(
                profile_name="video_realtime",
                variant_stable_id="v720",
                timeline_generation=0,
                discontinuity_sequence=0,
                sequence=10,
                media_revision="",
            ),
            segment=segment,
            admitted_at=0.0,
            last_seen_at=0.0,
        )
        proc_identity = scheduler._processing_identity(processor, item)
        assert proc_identity.storage_id == identity.storage_id
        assert proc_identity.storage_id != "channel-01"
        assert proc_identity.check_name == "black_screen"
        assert proc_identity.sequence == 10
    finally:
        scheduler.shutdown()


def test_global_media_process_gate_caps_cross_pool_analysis():
    class ActiveTracker:
        def __init__(self):
            self.lock = Lock()
            self.active = 0
            self.max_active = 0

        def enter(self):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)

        def leave(self):
            with self.lock:
                self.active -= 1

    class ConcurrentProfile:
        def __init__(self, resource_class, tracker):
            self.resource_class = resource_class
            self.tracker = tracker

        def analyze(self, _segment, *, requirements):
            self.tracker.enter()
            sleep(0.03)
            self.tracker.leave()
            if self.resource_class is AnalysisResourceClass.AUDIO_DECODE:
                return SegmentAnalysisBundle(
                    profile_name="audio_realtime",
                    audio_realtime=AudioRealtimeAnalysis(
                        checked=True,
                        presence=AudioTrackPresence.PRESENT,
                    ),
                )
            return SegmentAnalysisBundle(
                profile_name="video_realtime",
                video_realtime=VideoRealtimeAnalysis(checked=True),
            )

    tracker = ActiveTracker()
    profiles = (
        ConcurrentProfile(AnalysisResourceClass.VIDEO_DECODE, tracker),
        ConcurrentProfile(AnalysisResourceClass.AUDIO_DECODE, tracker),
    )
    processor = FakeProcessor()
    coordinator = ProfileWorkerCoordinator(
        FakeStateStore(),
        media_process_gate=BoundedSemaphore(2),
    )

    def analyze(index):
        return coordinator._analyze(
            profiles[index % 2],
            make_segment(index),
            [(processor, None)],
            set(),
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(analyze, range(5)))

    assert tracker.max_active == 2

import pytest

from core.profile_scheduler import ProfileScheduler
from core.analysis_profile import AnalysisResourceClass
from models.analysis import AnalysisRequirement
from models.stream import StreamIdentity


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
        max_workers=1,
        max_pending_tasks=0,
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
    finally:
        scheduler.shutdown()

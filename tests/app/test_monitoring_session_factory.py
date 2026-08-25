from types import SimpleNamespace

import pytest

from app.monitoring_session_factory import MonitoringSessionFactory
from core.redis_keys import RedisNamespace
from models.stream_config import StreamConfig
from models.audio import AudioTrackHint
from models.rendition import MediaRenditionKind
from tests.factories.hls import make_segment


def config(**options):
    return StreamConfig(
        master_url="https://example.test/master.m3u8",
        stream_id="stream-1",
        **options,
    )


def build(item):
    factory = MonitoringSessionFactory(
        namespace=RedisNamespace("monitor:test")
    )
    components = factory.build_detection_components(
        config=item,
        redis_client=SimpleNamespace(client=object()),
    )
    return components


def test_default_composition_enables_video_and_audio_checks():
    components = build(config())
    try:
        assert [profile.name for profile in components.profiles] == [
            "video_realtime",
            "audio_realtime",
        ]
        assert [processor.name for processor in components.processors] == [
            "black_screen",
            "audio_loss",
        ]
    finally:
        components.close()


def test_black_only_composition_does_not_create_audio_profile():
    components = build(config(audio_loss_enabled=False))
    try:
        assert [profile.name for profile in components.profiles] == [
            "video_realtime"
        ]
        assert [processor.name for processor in components.processors] == [
            "black_screen"
        ]
    finally:
        components.close()


def test_audio_only_composition_does_not_create_video_profile():
    components = build(config(black_screen_enabled=False))
    try:
        assert [profile.name for profile in components.profiles] == [
            "audio_realtime"
        ]
        assert [processor.name for processor in components.processors] == [
            "audio_loss"
        ]
    finally:
        components.close()


def test_audio_config_reaches_profile_command_and_policy():
    components = build(
        config(
            black_screen_enabled=False,
            silence_threshold_dbfs=-55.0,
            audio_loss_duration=45.0,
            audio_track_index=2,
        )
    )
    try:
        profile = components.profiles[0]
        processor = components.processors[0]
        assert profile.parsers[0].threshold_dbfs == -55.0
        assert profile.command_builder.track_index == 2
        assert processor.event_store.policy.alert_duration == 45.0
    finally:
        components.close()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (
            {"black_screen_enabled": False, "audio_loss_enabled": False},
            "At least one monitoring check",
        ),
        ({"silence_threshold_dbfs": 0.1}, "silence_threshold_dbfs"),
        ({"silence_threshold_dbfs": float("nan")}, "silence_threshold_dbfs"),
        ({"audio_loss_duration": 0.0}, "audio_loss_duration"),
        ({"audio_track_index": -1}, "audio_track_index"),
    ],
)
def test_stream_config_rejects_invalid_monitoring_options(options, message):
    with pytest.raises(ValueError, match=message):
        config(**options)


def test_factory_owns_one_service_wide_media_process_gate():
    factory = MonitoringSessionFactory(max_concurrent_media_processes=3)

    assert factory.service_media_process_gate is not None


def test_factory_rejects_invalid_service_media_process_budget():
    with pytest.raises(ValueError, match="max_concurrent_media_processes"):
        MonitoringSessionFactory(max_concurrent_media_processes=0)


def test_external_audio_is_scheduled_once_as_audio_rendition():
    components = build(config())
    try:
        video_segment = make_segment(1)
        video_segment.audio_track_hint = AudioTrackHint.EXTERNAL
        audio_segment = make_segment(
            1,
            variant_id="audio:main:English",
            variant_stable_id="audio-en",
        )
        audio_segment.has_video = False
        audio_segment.audio_track_hint = AudioTrackHint.MUXED
        audio_segment.rendition_kind = MediaRenditionKind.AUDIO
        profiles = {item.name: item for item in components.profiles}
        processors = {item.name: item for item in components.processors}

        assert profiles["audio_realtime"].supports_segment(video_segment) is False
        assert processors["audio_loss"].supports_segment(video_segment) is False
        assert profiles["audio_realtime"].supports_segment(audio_segment) is True
        assert processors["audio_loss"].supports_segment(audio_segment) is True
        assert profiles["video_realtime"].supports_segment(audio_segment) is False
        assert processors["black_screen"].supports_segment(audio_segment) is False
    finally:
        components.close()

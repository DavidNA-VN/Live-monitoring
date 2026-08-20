from __future__ import annotations

from collections.abc import Callable

from checks.black_screen.cross_variant import (
    RedisBlackCrossVariantCorrelator,
)
from checks.black_screen.live_state import RedisBlackEventStore
from checks.black_screen.processor import BlackScreenSegmentProcessor
from core.alert_stream import AlertSink
from core.live_runtime import LiveMonitoringRuntime, LiveRuntimeSettings
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import RedisKeyBuilder
from core.runtime_health import RedisRuntimeHealthReporter
from core.segment_state import RedisSegmentStateStore
from core.stream_session import StreamSession
from media.input_resolver import HlsMediaInputResolver
from models.stream_config import StreamConfig
from profiles.video_realtime import VideoRealtimeProfile


AlertSinkFactory = Callable[
    [StreamConfig, RedisKeyBuilder],
    AlertSink,
]


class BlackScreenSessionFactory:
    """Application assembly for one independently owned stream session."""

    def __init__(
        self,
        *,
        redis_settings: RedisSettings | None = None,
        key_builder: RedisKeyBuilder | None = None,
        alert_sink_factory: AlertSinkFactory | None = None,
    ) -> None:
        self.redis_settings = redis_settings
        self.keys = key_builder or RedisKeyBuilder()
        self.alert_sink_factory = alert_sink_factory

    def create(self, config: StreamConfig) -> StreamSession:
        redis_client = RedisClient(self.redis_settings)
        profile = None
        try:
            redis_client.ping()
            stream = config.identity
            runtime_settings = LiveRuntimeSettings(
                playlist_timeout=config.playlist_timeout,
                max_workers=config.max_decode_workers,
                max_pending_tasks=config.max_pending_tasks,
                max_admitted_work=config.max_admitted_work,
                max_work_age_seconds=config.max_work_age_seconds,
                max_segments_per_batch=config.max_segments_per_batch,
                media_playlist_workers=config.media_playlist_workers,
                request_headers=config.request_headers,
            )
            segment_state = RedisSegmentStateStore(
                redis_client=redis_client,
                key_builder=self.keys,
            )
            correlator = RedisBlackCrossVariantCorrelator(
                stream_id=stream.stream_id,
                redis_client=redis_client,
                key_builder=self.keys,
            )
            alert_sink = (
                self.alert_sink_factory(config, self.keys)
                if self.alert_sink_factory is not None
                else None
            )
            event_store = RedisBlackEventStore(
                stream_id=stream.stream_id,
                redis_client=redis_client,
                key_builder=self.keys,
                cross_variant_correlator=correlator,
                alert_stream_max_length=config.alert_stream_max_length,
                alert_sink=alert_sink,
            )
            profile = VideoRealtimeProfile(
                media_input_resolver=HlsMediaInputResolver(
                    request_headers=config.request_headers
                )
            )
            runtime = LiveMonitoringRuntime(
                stream=stream,
                state_store=segment_state,
                processors=[
                    BlackScreenSegmentProcessor(event_store=event_store)
                ],
                analysis_profiles=[profile],
                health_reporter=RedisRuntimeHealthReporter(
                    stream_id=stream.stream_id,
                    redis_client=redis_client,
                    key_builder=self.keys,
                    stream_max_length=config.alert_stream_max_length,
                ),
                settings=runtime_settings,
            )
            return StreamSession(
                config=config,
                runtime=runtime,
                close_callbacks=(profile.close, redis_client.close),
            )
        except Exception:
            if profile is not None:
                profile.close()
            redis_client.close()
            raise

from __future__ import annotations

from collections.abc import Callable

from checks.black_screen.live_state import RedisBlackEventStore
from checks.black_screen.processor import BlackScreenSegmentProcessor
from core.alert_stream import AlertSink
from core.live_runtime import LiveMonitoringRuntime, LiveRuntimeSettings
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import (
    AlertRedisKeys,
    ProcessingRedisKeys,
    RedisNamespace,
    RuntimeRedisKeys,
)
from core.runtime_health import RedisRuntimeHealthReporter
from core.segment_state import RedisSegmentStateStore
from core.stream_session import StreamSession
from media.input_resolver import HlsMediaInputResolver
from models.stream_config import StreamConfig
from profiles.video_realtime import VideoRealtimeProfile
from checks.black_screen.redis_keys import BlackScreenRedisKeys


AlertSinkFactory = Callable[
    [StreamConfig, RedisNamespace],
    AlertSink,
]


class BlackScreenSessionFactory:
    """Application assembly for one independently owned stream session."""

    def __init__(
        self,
        *,
        redis_settings: RedisSettings | None = None,
        namespace: RedisNamespace | None = None,
        alert_sink_factory: AlertSinkFactory | None = None,
    ) -> None:
        self.redis_settings = redis_settings
        self.namespace = namespace or RedisNamespace()
        self.processing_keys = ProcessingRedisKeys(self.namespace)
        self.runtime_keys = RuntimeRedisKeys(self.namespace)
        self.alert_keys = AlertRedisKeys(self.namespace)
        self.black_keys = BlackScreenRedisKeys(self.namespace)
        self.alert_sink_factory = alert_sink_factory

    def create(self, config: StreamConfig) -> StreamSession:
        redis_client = RedisClient(self.redis_settings)
        profile = None
        try:
            redis_client.ping()
            stream = config.identity
            runtime_settings = LiveRuntimeSettings(
                playlist_timeout=config.playlist_timeout,
                resource_limits=config.resource_limits,
                max_concurrent_media_processes=(
                    config.max_concurrent_media_processes
                ),
                max_admitted_work=config.max_admitted_work,
                max_work_age_seconds=config.max_work_age_seconds,
                max_segments_per_batch=config.max_segments_per_batch,
                media_playlist_workers=config.media_playlist_workers,
                request_headers=config.request_headers,
            )
            segment_state = RedisSegmentStateStore(
                redis_client=redis_client,
                processing_keys=self.processing_keys,
            )
            alert_sink = (
                self.alert_sink_factory(config, self.namespace)
                if self.alert_sink_factory is not None
                else None
            )
            event_store = RedisBlackEventStore(
                stream_id=stream.stream_id,
                redis_client=redis_client,
                black_keys=self.black_keys,
                alert_keys=self.alert_keys,
                runtime_keys=self.runtime_keys,
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
                runtime_keys=self.runtime_keys,
                health_reporter=RedisRuntimeHealthReporter(
                    stream_id=stream.stream_id,
                    redis_client=redis_client,
                    runtime_keys=self.runtime_keys,
                    alert_keys=self.alert_keys,
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

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import BoundedSemaphore

from checks.audio_loss.live_state import RedisAudioLossEventStore
from checks.audio_loss.processor import AudioLossSegmentProcessor
from checks.audio_loss.redis_keys import AudioLossRedisKeys
from checks.black_screen.live_state import RedisBlackEventStore
from checks.black_screen.processor import BlackScreenSegmentProcessor
from checks.black_screen.redis_keys import BlackScreenRedisKeys
from core.alert_stream import AlertSink
from core.analysis_profile import AnalysisProfile
from core.live_runtime import LiveMonitoringRuntime, LiveRuntimeSettings
from core.redis_client import RedisClient, RedisSettings
from core.redis_keys import (
    AlertRedisKeys,
    ProcessingRedisKeys,
    RedisNamespace,
    RuntimeRedisKeys,
)
from core.runtime_health import RedisRuntimeHealthReporter
from core.segment_processor import SegmentProcessor
from core.segment_state import RedisSegmentStateStore
from core.stream_session import StreamSession
from media.input_resolver import HlsMediaInputResolver
from models.stream_config import StreamConfig
from policies.audio_loss import AudioLossAlertPolicy
from profiles.audio_realtime import AudioRealtimeProfile
from profiles.video_realtime import VideoRealtimeProfile


AlertSinkFactory = Callable[
    [StreamConfig, RedisNamespace],
    AlertSink,
]


@dataclass(frozen=True)
class DetectionComponents:
    profiles: tuple[AnalysisProfile, ...]
    processors: tuple[SegmentProcessor, ...]

    def close(self) -> None:
        for profile in reversed(self.profiles):
            profile.close()


class MonitoringSessionFactory:
    """Application assembly for one independently owned stream session."""

    def __init__(
        self,
        *,
        redis_settings: RedisSettings | None = None,
        namespace: RedisNamespace | None = None,
        alert_sink_factory: AlertSinkFactory | None = None,
        max_concurrent_media_processes: int = 8,
    ) -> None:
        if max_concurrent_media_processes <= 0:
            raise ValueError("max_concurrent_media_processes must be > 0")
        self.redis_settings = redis_settings
        self.namespace = namespace or RedisNamespace()
        self.processing_keys = ProcessingRedisKeys(self.namespace)
        self.runtime_keys = RuntimeRedisKeys(self.namespace)
        self.alert_keys = AlertRedisKeys(self.namespace)
        self.black_keys = BlackScreenRedisKeys(self.namespace)
        self.audio_keys = AudioLossRedisKeys(self.namespace)
        self.alert_sink_factory = alert_sink_factory
        self.service_media_process_gate = BoundedSemaphore(
            max_concurrent_media_processes
        )

    def create(self, config: StreamConfig) -> StreamSession:
        redis_client = RedisClient(self.redis_settings)
        components: DetectionComponents | None = None
        try:
            redis_client.ping()
            stream = config.identity
            alert_sink = (
                self.alert_sink_factory(config, self.namespace)
                if self.alert_sink_factory is not None
                else None
            )
            components = self.build_detection_components(
                config=config,
                redis_client=redis_client,
                alert_sink=alert_sink,
            )
            runtime = LiveMonitoringRuntime(
                stream=stream,
                state_store=RedisSegmentStateStore(
                    redis_client=redis_client,
                    processing_keys=self.processing_keys,
                ),
                processors=list(components.processors),
                analysis_profiles=list(components.profiles),
                runtime_keys=self.runtime_keys,
                health_reporter=RedisRuntimeHealthReporter(
                    stream_id=stream.storage_id,
                    redis_client=redis_client,
                    runtime_keys=self.runtime_keys,
                    alert_keys=self.alert_keys,
                    stream_max_length=config.alert_stream_max_length,
                ),
                settings=self._runtime_settings(config),
                service_media_process_gate=self.service_media_process_gate,
            )
            return StreamSession(
                config=config,
                runtime=runtime,
                close_callbacks=(components.close, redis_client.close),
            )
        except Exception:
            if components is not None:
                components.close()
            redis_client.close()
            raise

    def build_detection_components(
        self,
        *,
        config: StreamConfig,
        redis_client: RedisClient,
        alert_sink: AlertSink | None = None,
    ) -> DetectionComponents:
        profiles: list[AnalysisProfile] = []
        processors: list[SegmentProcessor] = []
        try:
            if config.black_screen_enabled:
                black_profile = VideoRealtimeProfile(
                    media_input_resolver=self._media_resolver(config)
                )
                profiles.append(black_profile)
                processors.append(
                    BlackScreenSegmentProcessor(
                        event_store=RedisBlackEventStore(
                            stream_id=config.identity.storage_id,
                            redis_client=redis_client,
                            black_keys=self.black_keys,
                            alert_keys=self.alert_keys,
                            runtime_keys=self.runtime_keys,
                            alert_stream_max_length=(
                                config.alert_stream_max_length
                            ),
                            alert_sink=alert_sink,
                        )
                    )
                )

            if config.audio_loss_enabled:
                audio_profile = AudioRealtimeProfile(
                    threshold_dbfs=config.silence_threshold_dbfs,
                    track_index=config.audio_track_index,
                    media_input_resolver=self._media_resolver(config),
                )
                profiles.append(audio_profile)
                processors.append(
                    AudioLossSegmentProcessor(
                        event_store=RedisAudioLossEventStore(
                            stream_id=config.identity.storage_id,
                            redis_client=redis_client,
                            policy=AudioLossAlertPolicy(
                                alert_duration=config.audio_loss_duration
                            ),
                            audio_keys=self.audio_keys,
                            alert_keys=self.alert_keys,
                            runtime_keys=self.runtime_keys,
                            threshold_dbfs=config.silence_threshold_dbfs,
                            alert_stream_max_length=(
                                config.alert_stream_max_length
                            ),
                            alert_sink=alert_sink,
                        )
                    )
                )
        except Exception:
            for profile in reversed(profiles):
                profile.close()
            raise
        return DetectionComponents(
            profiles=tuple(profiles),
            processors=tuple(processors),
        )

    @staticmethod
    def _runtime_settings(config: StreamConfig) -> LiveRuntimeSettings:
        return LiveRuntimeSettings(
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

    @staticmethod
    def _media_resolver(config: StreamConfig) -> HlsMediaInputResolver:
        return HlsMediaInputResolver(request_headers=config.request_headers)

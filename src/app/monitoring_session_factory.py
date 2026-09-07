from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from checks.audio_loss.live_state import RedisAudioLossEventStore
from checks.audio_loss.processor import AudioLossSegmentProcessor
from checks.audio_loss.redis_keys import AudioLossRedisKeys
from checks.black_screen.live_state import RedisBlackEventStore
from checks.black_screen.processor import BlackScreenSegmentProcessor
from checks.black_screen.redis_keys import BlackScreenRedisKeys
from checks.video_freeze.live_state import RedisVideoFreezeEventStore
from checks.video_freeze.processor import VideoFreezeSegmentProcessor
from checks.video_freeze.redis_keys import VideoFreezeRedisKeys
from core.alert_stream import AlertSink
from core.analysis_profile import AnalysisProfile
from core.live_runtime import LiveMonitoringRuntime, LiveRuntimeSettings
from core.media_process_budget import ObservableProcessGate, ProcessGate
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
from models.analysis import AnalysisResourceClass, ResourcePoolLimit
from policies.audio_loss import AudioLossAlertPolicy
from policies.video_freeze import VideoFreezeAlertPolicy
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
        per_stream_media_processes: int | None = None,
        resource_limits: Mapping[
            AnalysisResourceClass, ResourcePoolLimit
        ] | None = None,
        service_media_process_gate: ObservableProcessGate | ProcessGate | None = None,
    ) -> None:
        if max_concurrent_media_processes <= 0:
            raise ValueError("max_concurrent_media_processes must be > 0")
        if (
            per_stream_media_processes is not None
            and per_stream_media_processes <= 0
        ):
            raise ValueError("per_stream_media_processes must be > 0")
        self.redis_settings = redis_settings
        self.namespace = namespace or RedisNamespace()
        self.processing_keys = ProcessingRedisKeys(self.namespace)
        self.runtime_keys = RuntimeRedisKeys(self.namespace)
        self.alert_keys = AlertRedisKeys(self.namespace)
        self.black_keys = BlackScreenRedisKeys(self.namespace)
        self.freeze_keys = VideoFreezeRedisKeys(self.namespace)
        self.audio_keys = AudioLossRedisKeys(self.namespace)
        self.alert_sink_factory = alert_sink_factory
        self.per_stream_media_processes = per_stream_media_processes
        self.resource_limits = dict(resource_limits) if resource_limits else None
        self.service_media_process_gate = (
            service_media_process_gate
            or ObservableProcessGate(max_concurrent_media_processes)
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
                    storage_id=stream.storage_id,
                    external_stream_id=stream.external_stream_id,
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
        identity = config.identity
        try:
            if config.black_screen_enabled or config.video_freeze_enabled:
                video_profile = VideoRealtimeProfile(
                    freeze_noise_db=config.freeze_noise_db,
                    freeze_detector_minimum_duration=(
                        config.freeze_detector_minimum_duration
                    ),
                    media_input_resolver=self._media_resolver(config),
                )
                profiles.append(video_profile)

            if config.black_screen_enabled:
                processors.append(
                    BlackScreenSegmentProcessor(
                        event_store=RedisBlackEventStore(
                            storage_id=identity.storage_id,
                            external_stream_id=identity.external_stream_id,
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

            if config.video_freeze_enabled:
                processors.append(
                    VideoFreezeSegmentProcessor(
                        event_store=RedisVideoFreezeEventStore(
                            storage_id=identity.storage_id,
                            external_stream_id=identity.external_stream_id,
                            redis_client=redis_client,
                            policy=VideoFreezeAlertPolicy(
                                warning_duration=(
                                    config.freeze_warning_duration
                                ),
                                alert_duration=config.freeze_alert_duration,
                            ),
                            freeze_keys=self.freeze_keys,
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
                            storage_id=identity.storage_id,
                            external_stream_id=identity.external_stream_id,
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

    def _runtime_settings(self, config: StreamConfig) -> LiveRuntimeSettings:
        return LiveRuntimeSettings(
            playlist_timeout=config.playlist_timeout,
            resource_limits=self.resource_limits or config.resource_limits,
            max_concurrent_media_processes=(
                self.per_stream_media_processes
                if self.per_stream_media_processes is not None
                else config.max_concurrent_media_processes
            ),
            max_admitted_work=config.max_admitted_work,
            max_work_age_seconds=config.max_work_age_seconds,
            max_segments_per_batch=config.max_segments_per_batch,
            media_playlist_workers=config.media_playlist_workers,
            request_headers=config.request_headers,
            admission_policy=config.admission_policy,
            variant_selection=config.variant_selection,
        )

    @staticmethod
    def _media_resolver(config: StreamConfig) -> HlsMediaInputResolver:
        return HlsMediaInputResolver(request_headers=config.request_headers)

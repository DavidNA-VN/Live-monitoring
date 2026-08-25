from __future__ import annotations

from uuid import uuid4

import redis

from checks.audio_loss.alert_publisher import AudioLossAlertPublisher
from checks.audio_loss.event_reducer import (
    AudioLossEventReducer,
    AudioLossEventTransitionType,
)
from checks.audio_loss.event_repository import RedisAudioLossEventRepository
from checks.audio_loss.redis_keys import AudioLossRedisKeys
from core.alert_stream import AlertSink
from core.redis_client import RedisClient, RedisUnavailableError
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from core.redis_scripts import RELEASE_OWNED_LOCK
from models.audio_loss import AudioLossDetectionResult, AudioLossLiveEvent
from models.segment import Segment
from policies.audio_loss import AudioLossAlertPolicy


class AudioLossEventStateBusyError(RuntimeError):
    pass


class RedisAudioLossEventStore:
    """Coordinates locking, pure reduction and atomic event persistence."""

    def __init__(
        self,
        stream_id: str,
        redis_client: RedisClient,
        policy: AudioLossAlertPolicy | None = None,
        audio_keys: AudioLossRedisKeys | None = None,
        alert_keys: AlertRedisKeys | None = None,
        runtime_keys: RuntimeRedisKeys | None = None,
        threshold_dbfs: float = -60.0,
        boundary_tolerance: float = 0.10,
        event_ttl_seconds: int = 86_400,
        commit_ttl_seconds: int = 21_600,
        event_lock_ms: int = 30_000,
        alert_stream_max_length: int = 10_000,
        alert_sink: AlertSink | None = None,
        reducer: AudioLossEventReducer | None = None,
    ) -> None:
        if event_lock_ms <= 0:
            raise ValueError("event_lock_ms must be > 0")
        self.stream_id = stream_id
        self.redis = redis_client.client
        self.policy = policy or AudioLossAlertPolicy()
        namespace = (
            audio_keys.namespace if audio_keys is not None else RedisNamespace()
        )
        self.keys = audio_keys or AudioLossRedisKeys(namespace)
        self.alert_keys = alert_keys or AlertRedisKeys(namespace)
        self.runtime_keys = runtime_keys or RuntimeRedisKeys(namespace)
        self.event_lock_ms = event_lock_ms
        self.reducer = reducer or AudioLossEventReducer(
            stream_id=stream_id,
            boundary_tolerance=boundary_tolerance,
        )
        alerts = AudioLossAlertPublisher(
            stream_id=stream_id,
            alert_keys=self.alert_keys,
            runtime_keys=self.runtime_keys,
            threshold_dbfs=threshold_dbfs,
            threshold_duration=self.policy.alert_duration,
            stream_max_length=alert_stream_max_length,
            alert_sink=alert_sink,
        )
        self.repository = RedisAudioLossEventRepository(
            stream_id=stream_id,
            redis_client=self.redis,
            audio_keys=self.keys,
            event_ttl_seconds=event_ttl_seconds,
            commit_ttl_seconds=commit_ttl_seconds,
            alerts=alerts,
        )

    def apply(
        self,
        *,
        segment: Segment,
        result: AudioLossDetectionResult,
    ) -> None:
        if not result.checked:
            raise ValueError("Unchecked audio-loss result cannot be committed")
        commit_key = self.keys.commit_marker(
            self.stream_id,
            segment.variant_stable_id,
            segment.discontinuity_sequence,
            segment.sequence,
            segment.timeline_generation,
            segment.media_revision,
        )
        try:
            if self.redis.exists(commit_key):
                return
            lock_key = self.keys.event_lock(
                self.stream_id,
                segment.variant_stable_id,
            )
            token = uuid4().hex
            acquired = self.redis.set(
                lock_key,
                token,
                nx=True,
                px=self.event_lock_ms,
            )
        except redis.RedisError as exc:
            raise RedisUnavailableError(str(exc)) from exc
        if not acquired:
            raise AudioLossEventStateBusyError(
                f"Audio-loss event state is busy for {segment.variant_id}"
            )

        try:
            if self.redis.exists(commit_key):
                return
            self._apply_locked(segment, result, commit_key)
        except redis.RedisError as exc:
            raise RedisUnavailableError(str(exc)) from exc
        finally:
            try:
                self.redis.eval(RELEASE_OWNED_LOCK, 1, lock_key, token)
            except redis.RedisError:
                pass

    def _apply_locked(
        self,
        segment: Segment,
        result: AudioLossDetectionResult,
        commit_key: str,
    ) -> None:
        transitions = self.reducer.reduce(
            open_event=self.repository.load_open(segment.variant_stable_id),
            segment=segment,
            result=result,
        )
        if not transitions:
            return

        pipeline = self.redis.pipeline(transaction=True)
        commits_segment = False
        for transition in transitions:
            commits_segment = commits_segment or transition.commits_segment
            if transition.type is AudioLossEventTransitionType.MARK_COMMITTED:
                continue
            if transition.event is None:
                raise RuntimeError("audio-loss event transition is incomplete")
            if transition.type is AudioLossEventTransitionType.PERSIST_OPEN:
                self._queue_persist_open(pipeline, transition.event)
            elif transition.type is AudioLossEventTransitionType.RESOLVE:
                if transition.reason is None:
                    raise RuntimeError("audio-loss resolve reason is missing")
                self._queue_resolve(
                    pipeline,
                    transition.event,
                    transition.reason,
                )

        if not commits_segment:
            raise RuntimeError("audio-loss reduction did not commit the segment")
        self.repository.queue_commit(pipeline, commit_key)
        pipeline.execute()

    def _queue_persist_open(self, pipeline, event: AudioLossLiveEvent) -> None:
        should_alert = (
            not event.alert_sent
            and self.policy.should_alert(event.duration)
        )
        if should_alert:
            event.alert_sent = True
        self.repository.queue_persist_open(
            pipeline,
            event=event,
            alert=should_alert,
        )

    def _queue_resolve(
        self,
        pipeline,
        event: AudioLossLiveEvent,
        reason: str,
    ) -> None:
        alert_on_resolution = (
            not event.alert_sent
            and self.policy.should_alert(event.duration)
        )
        if alert_on_resolution:
            event.alert_sent = True
        self.repository.queue_resolve(
            pipeline,
            event=event,
            reason=reason,
            alert_on_resolution=alert_on_resolution,
        )

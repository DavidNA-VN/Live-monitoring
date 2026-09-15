from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import redis

from checks.video_freeze.alert_publisher import VideoFreezeAlertPublisher
from checks.video_freeze.event_reducer import (
    VideoFreezeEventReducer,
    VideoFreezeEventTransitionType,
)
from checks.video_freeze.event_repository import (
    RedisVideoFreezeEventRepository,
)
from checks.video_freeze.redis_keys import VideoFreezeRedisKeys
from checks.video_freeze.recovery_repository import RedisVideoFreezeAlertRecoveryRepository
from checks.video_freeze.repeated_repository import (
    RedisRepeatedFreezeRepository,
)
from core.alert_stream import AlertSink
from core.redis_client import RedisClient, RedisUnavailableError
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from core.redis_scripts import RELEASE_OWNED_LOCK
from models.freeze import (
    VideoFreezeAlertRecoveryState,
    VideoFreezeAlertType,
    VideoFreezeDetectionResult,
    VideoFreezeLiveEvent,
    VideoFreezeSeverity,
)
from models.segment import Segment
from policies.video_freeze import VideoFreezeAlertPolicy


class VideoFreezeEventStateBusyError(RuntimeError):
    pass


class RedisVideoFreezeEventStore:
    """Serializes reduction and atomically persists freeze lifecycle state."""

    def __init__(
        self,
        *,
        storage_id: str,
        external_stream_id: str,
        redis_client: RedisClient,
        policy: VideoFreezeAlertPolicy | None = None,
        freeze_keys: VideoFreezeRedisKeys | None = None,
        alert_keys: AlertRedisKeys | None = None,
        runtime_keys: RuntimeRedisKeys | None = None,
        boundary_tolerance: float = 0.10,
        event_ttl_seconds: int = 86_400,
        commit_ttl_seconds: int = 21_600,
        event_lock_ms: int = 30_000,
        alert_stream_max_length: int = 10_000,
        alert_sink: AlertSink | None = None,
        reducer: VideoFreezeEventReducer | None = None,
        recovery_repository: RedisVideoFreezeAlertRecoveryRepository | None = None,
    ) -> None:
        if event_lock_ms <= 0:
            raise ValueError("event_lock_ms must be > 0")
        self.storage_id = storage_id
        self.external_stream_id = external_stream_id
        self.redis = redis_client.client
        self.policy = policy or VideoFreezeAlertPolicy()
        namespace = (
            freeze_keys.namespace
            if freeze_keys is not None
            else RedisNamespace()
        )
        self.keys = freeze_keys or VideoFreezeRedisKeys(namespace)
        alert_keys = alert_keys or AlertRedisKeys(namespace)
        runtime_keys = runtime_keys or RuntimeRedisKeys(namespace)
        self.event_lock_ms = event_lock_ms
        self.reducer = reducer or VideoFreezeEventReducer(
            storage_id=storage_id,
            external_stream_id=external_stream_id,
            boundary_tolerance=boundary_tolerance,
        )
        alerts = VideoFreezeAlertPublisher(
            storage_id=storage_id,
            external_stream_id=external_stream_id,
            alert_keys=alert_keys,
            runtime_keys=runtime_keys,
            policy=self.policy,
            stream_max_length=alert_stream_max_length,
            alert_sink=alert_sink,
        )
        self.repository = RedisVideoFreezeEventRepository(
            storage_id=storage_id,
            redis_client=self.redis,
            freeze_keys=self.keys,
            event_ttl_seconds=event_ttl_seconds,
            commit_ttl_seconds=commit_ttl_seconds,
            alerts=alerts,
        )
        self.repeated = RedisRepeatedFreezeRepository(
            storage_id=storage_id,
            redis_client=self.redis,
            policy=self.policy,
            freeze_keys=self.keys,
            event_ttl_seconds=event_ttl_seconds,
            alerts=alerts,
        )
        self.recovery = recovery_repository or RedisVideoFreezeAlertRecoveryRepository(
            storage_id=storage_id, redis_client=self.redis, freeze_keys=self.keys,
            ttl_seconds=event_ttl_seconds,
        )

    def apply(
        self,
        *,
        segment: Segment,
        result: VideoFreezeDetectionResult,
    ) -> None:
        commit_key = self.keys.commit_marker(
            self.storage_id,
            segment.variant_stable_id,
            segment.discontinuity_sequence,
            segment.sequence,
            segment.timeline_generation,
            segment.media_revision,
        )
        lock_key = self.keys.event_lock(
            self.storage_id, segment.variant_stable_id
        )
        token = uuid4().hex
        try:
            if self.redis.exists(commit_key):
                return
            acquired = self.redis.set(
                lock_key, token, nx=True, px=self.event_lock_ms
            )
        except redis.RedisError as exc:
            raise RedisUnavailableError(str(exc)) from exc
        if not acquired:
            raise VideoFreezeEventStateBusyError(
                f"Video-freeze state is busy for {segment.variant_id}"
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
        result: VideoFreezeDetectionResult,
        commit_key: str,
    ) -> None:
        transitions = self.reducer.reduce(
            open_event=self.repository.load_open(
                segment.variant_stable_id
            ),
            segment=segment,
            result=result,
        )
        recovery = self.recovery.load(segment.variant_stable_id)
        if not transitions:
            return

        pipeline = self.redis.pipeline(transaction=True)
        commits_segment = False
        notification_state: dict[
            str, tuple[bool, bool, VideoFreezeSeverity | None]
        ] = {}
        pending_repeated_state = None
        interrupted_recorded = False
        for transition in transitions:
            commits_segment = commits_segment or transition.commits_segment
            if transition.type in (
                VideoFreezeEventTransitionType.MARK_COMMITTED,
            ):
                continue
            if transition.type is VideoFreezeEventTransitionType.HEALTHY_EVIDENCE:
                recovery = self._healthy(
                    segment, recovery, pipeline, pending_repeated_state
                )
                pending_repeated_state = None
                continue
            if transition.type is VideoFreezeEventTransitionType.UNKNOWN_GAP:
                if transition.event is not None:
                    self.repository.queue_close_canonical(pipeline, event=transition.event)
                    if transition.event.alert_sent and recovery is None:
                        recovery = self._new_recovery(transition.event, VideoFreezeAlertType.CONTINUOUS)
                if recovery is not None:
                    recovery = self._continue_recovery(recovery, segment, pipeline)
                if not interrupted_recorded:
                    self.repository.alerts.add_interrupted_metric(pipeline)
                    interrupted_recorded = True
                continue
            if transition.event is None:
                raise RuntimeError("video-freeze transition has no event")
            prior = notification_state.get(transition.event.event_id)
            if prior is not None:
                (
                    transition.event.warning_sent,
                    transition.event.alert_sent,
                    transition.event.highest_severity,
                ) = prior
            if transition.type is VideoFreezeEventTransitionType.PERSIST_OPEN:
                if recovery is not None:
                    recovery = self._continue_recovery(recovery, segment, pipeline)
                self._queue_persist_open(pipeline, transition.event, allow_alert=recovery is None)
            elif transition.type in (
                VideoFreezeEventTransitionType.CLOSE_EVENT,
                VideoFreezeEventTransitionType.RESOLVE,
            ):
                if transition.reason is None:
                    raise RuntimeError("video-freeze resolve reason is missing")
                recovery, pending_repeated_state = self._queue_close(
                    pipeline, transition.event, segment, recovery
                )
            notification_state[transition.event.event_id] = (
                transition.event.warning_sent,
                transition.event.alert_sent,
                transition.event.highest_severity,
            )

        if not commits_segment:
            raise RuntimeError("video-freeze reduction did not commit segment")
        self.repository.queue_commit(pipeline, commit_key)
        pipeline.execute()

    def _queue_persist_open(
        self,
        pipeline,
        event: VideoFreezeLiveEvent,
        *,
        allow_alert: bool = True,
    ) -> None:
        severity = self.policy.pending_notification(
            duration=event.duration,
            warning_sent=event.warning_sent,
            alert_sent=event.alert_sent,
        ) if allow_alert else None
        notification = None
        if severity is VideoFreezeSeverity.WARNING:
            event.warning_sent = True
            event.highest_severity = VideoFreezeSeverity.WARNING
            notification = ("OPEN", severity, "freeze_warning_threshold")
        elif severity is VideoFreezeSeverity.ALERT:
            state = "UPDATE" if event.warning_sent else "OPEN"
            event.alert_sent = True
            event.highest_severity = VideoFreezeSeverity.ALERT
            notification = (state, severity, "freeze_alert_threshold")
        self.repository.queue_persist_open(
            pipeline,
            event=event,
            notification=notification,
        )

    def _queue_close(self, pipeline, event, segment, recovery):
        pending = self.policy.pending_notification(
            duration=event.duration,
            warning_sent=event.warning_sent,
            alert_sent=event.alert_sent,
        )
        if pending is VideoFreezeSeverity.ALERT and recovery is None:
            event.alert_sent = True
            event.highest_severity = VideoFreezeSeverity.ALERT
            self.repository.queue_persist_open(
                pipeline, event=event,
                notification=("OPEN", pending, "freeze_alert_threshold"),
            )
        self.repository.queue_close_canonical(pipeline, event=event)

        repeated_state = None
        if self.policy.is_repeated_candidate(
            event.duration, event.reference_segment_duration,
        ):
            reduction = self.repeated.record_closed_event(event=event, pipeline=pipeline)
            repeated_state = reduction.state
            if reduction.alert is not None and reduction.alert.state == "OPEN":
                recovery = self._new_recovery(event, VideoFreezeAlertType.REPEATED)

        if event.alert_sent and recovery is None:
            recovery = self._new_recovery(event, VideoFreezeAlertType.CONTINUOUS)
        if recovery is not None:
            recovery = replace(recovery, last_observed_sequence=event.end_sequence)
            self.recovery.save(pipeline, segment.variant_stable_id, recovery)
        return recovery, repeated_state

    def _healthy(self, segment, recovery, pipeline, repeated_state=None):
        if recovery is None or not recovery.recovery_pending:
            return recovery
        if not self._same_timeline(recovery, segment):
            self._resolve_recovery(
                recovery, segment, "timeline_discontinued", pipeline,
                repeated_state,
            )
            self.recovery.delete(pipeline, segment.variant_stable_id)
            return None
        if segment.sequence != recovery.last_observed_sequence + 1:
            return self._continue_recovery(recovery, segment, pipeline)
        count = recovery.healthy_segments_observed + 1
        if self.policy.recovery_confirmed(count):
            self._resolve_recovery(
                recovery, segment, "healthy_segment_confirmed", pipeline,
                repeated_state,
            )
            self.recovery.delete(pipeline, segment.variant_stable_id)
            return None
        updated = replace(recovery, healthy_segments_observed=count,
                          last_observed_sequence=segment.sequence)
        self.recovery.save(pipeline, segment.variant_stable_id, updated)
        return updated

    def _continue_recovery(self, recovery, segment, pipeline):
        if not self._same_timeline(recovery, segment):
            self._resolve_recovery(recovery, segment, "timeline_discontinued", pipeline)
            self.recovery.delete(pipeline, segment.variant_stable_id)
            return None
        updated = replace(recovery, healthy_segments_observed=0,
                          last_observed_sequence=segment.sequence)
        self.recovery.save(pipeline, segment.variant_stable_id, updated)
        return updated

    def _resolve_recovery(self, recovery, segment, reason, pipeline, repeated_state=None):
        if recovery.alert_type is VideoFreezeAlertType.CONTINUOUS:
            event = self.repository.load_event(
                segment.variant_stable_id, recovery.alert_event_id
            )
            if event is None:
                raise RuntimeError("freeze recovery references a missing event")
            self.repository.queue_resolve(
                pipeline, event=event, opening_notification=None,
                resolution_severity=VideoFreezeSeverity.ALERT, reason=reason,
            )
            return
        alert = self.repeated.resolve_confirmed_recovery(
            segment=segment, timeline_generation=recovery.timeline_generation,
            reason=reason, pipeline=pipeline, state=repeated_state,
        )
        if alert is None:
            raise RuntimeError("freeze recovery references a missing repeated incident")

    @staticmethod
    def _new_recovery(event, alert_type):
        return VideoFreezeAlertRecoveryState(
            alert_event_id=event.event_id, alert_type=alert_type,
            recovery_pending=True, last_observed_sequence=event.end_sequence,
            timeline_generation=event.timeline_generation,
            discontinuity_sequence=event.discontinuity_sequence,
        )

    @staticmethod
    def _same_timeline(recovery, segment):
        return (
            recovery.timeline_generation == segment.timeline_generation
            and recovery.discontinuity_sequence == segment.discontinuity_sequence
        )

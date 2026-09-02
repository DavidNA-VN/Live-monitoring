from __future__ import annotations

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
from checks.video_freeze.repeated_repository import (
    RedisRepeatedFreezeRepository,
)
from core.alert_stream import AlertSink
from core.redis_client import RedisClient, RedisUnavailableError
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from core.redis_scripts import RELEASE_OWNED_LOCK
from models.freeze import (
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

    def apply(
        self,
        *,
        segment: Segment,
        result: VideoFreezeDetectionResult,
    ) -> None:
        if not result.checked:
            raise ValueError("Unchecked video-freeze result cannot be committed")
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
            self.repeated.resolve_if_quiet(segment)
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
        if not transitions:
            return

        pipeline = self.redis.pipeline(transaction=True)
        commits_segment = False
        notification_state: dict[
            str, tuple[bool, bool, VideoFreezeSeverity | None]
        ] = {}
        repeated_candidates: list[VideoFreezeLiveEvent] = []
        for transition in transitions:
            commits_segment = commits_segment or transition.commits_segment
            if transition.type is VideoFreezeEventTransitionType.MARK_COMMITTED:
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
                self._queue_persist_open(pipeline, transition.event)
            elif transition.type is VideoFreezeEventTransitionType.RESOLVE:
                if transition.reason is None:
                    raise RuntimeError("video-freeze resolve reason is missing")
                self._queue_resolve(
                    pipeline,
                    transition.event,
                    transition.reason,
                    repeated_candidates,
                )
            notification_state[transition.event.event_id] = (
                transition.event.warning_sent,
                transition.event.alert_sent,
                transition.event.highest_severity,
            )

        if not commits_segment:
            raise RuntimeError("video-freeze reduction did not commit segment")
        repeated_by_timeline: dict[
            tuple[str, int], list[VideoFreezeLiveEvent]
        ] = {}
        for event in repeated_candidates:
            repeated_by_timeline.setdefault(
                (event.variant_stable_id, event.timeline_generation), []
            ).append(event)
        for events in repeated_by_timeline.values():
            self.repeated.queue_records(pipeline, events)
        self.repository.queue_commit(pipeline, commit_key)
        pipeline.execute()

    def _queue_persist_open(
        self,
        pipeline,
        event: VideoFreezeLiveEvent,
    ) -> None:
        severity = self.policy.pending_notification(
            duration=event.duration,
            warning_sent=event.warning_sent,
            alert_sent=event.alert_sent,
        )
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

    def _queue_resolve(
        self,
        pipeline,
        event: VideoFreezeLiveEvent,
        reason: str,
        repeated_candidates: list[VideoFreezeLiveEvent],
    ) -> None:
        pending = self.policy.pending_notification(
            duration=event.duration,
            warning_sent=event.warning_sent,
            alert_sent=event.alert_sent,
        )
        opening = None
        if pending is VideoFreezeSeverity.WARNING:
            event.warning_sent = True
            event.highest_severity = VideoFreezeSeverity.WARNING
            opening = ("OPEN", pending, "freeze_warning_threshold")
        elif pending is VideoFreezeSeverity.ALERT:
            state = "UPDATE" if event.warning_sent else "OPEN"
            event.alert_sent = True
            event.highest_severity = VideoFreezeSeverity.ALERT
            opening = (state, pending, "freeze_alert_threshold")

        public_severity = event.highest_severity
        self.repository.queue_resolve(
            pipeline,
            event=event,
            opening_notification=opening,
            resolution_severity=public_severity,
            reason=reason,
        )
        if self.policy.is_repeated_candidate(event.duration):
            repeated_candidates.append(event)

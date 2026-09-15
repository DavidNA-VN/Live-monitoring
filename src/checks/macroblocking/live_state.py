from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import redis

from checks.macroblocking.alert_publisher import MacroblockingAlertPublisher
from checks.macroblocking.event_reducer import (
    MacroblockingEventReducer,
    MacroblockingEventTransitionType,
)
from checks.macroblocking.event_repository import (
    RedisMacroblockingEventRepository,
)
from checks.macroblocking.recovery_repository import (
    RedisMacroblockingRecoveryRepository,
)
from checks.macroblocking.redis_keys import MacroblockingRedisKeys
from core.alert_stream import AlertSink
from core.redis_client import RedisClient, RedisUnavailableError
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from core.redis_scripts import RELEASE_OWNED_LOCK
from models.macroblocking import (
    MacroblockingAlertRecoveryState,
    MacroblockingDetectionResult,
    MacroblockingEventStatus,
)
from models.segment import Segment
from policies.macroblocking import MacroblockingAlertPolicy


class MacroblockingEventStateBusyError(RuntimeError):
    pass


class RedisMacroblockingEventStore:
    """Atomically persists canonical state, recovery, metrics, and alerts."""

    def __init__(
        self,
        *,
        storage_id: str,
        external_stream_id: str,
        redis_client: RedisClient,
        policy: MacroblockingAlertPolicy | None = None,
        macroblocking_keys: MacroblockingRedisKeys | None = None,
        alert_keys: AlertRedisKeys | None = None,
        runtime_keys: RuntimeRedisKeys | None = None,
        event_ttl_seconds: int = 86_400,
        commit_ttl_seconds: int = 21_600,
        event_lock_ms: int = 30_000,
        alert_stream_max_length: int = 10_000,
        alert_sink: AlertSink | None = None,
        reducer: MacroblockingEventReducer | None = None,
    ) -> None:
        if event_lock_ms <= 0:
            raise ValueError("event_lock_ms must be > 0")
        self.storage_id = storage_id
        self.external_stream_id = external_stream_id
        self.redis = redis_client.client
        self.policy = policy or MacroblockingAlertPolicy()
        namespace = (
            macroblocking_keys.namespace
            if macroblocking_keys is not None
            else RedisNamespace()
        )
        self.keys = macroblocking_keys or MacroblockingRedisKeys(namespace)
        alert_keys = alert_keys or AlertRedisKeys(namespace)
        runtime_keys = runtime_keys or RuntimeRedisKeys(namespace)
        self.event_lock_ms = event_lock_ms
        self.reducer = reducer or MacroblockingEventReducer(
            storage_id=storage_id,
            external_stream_id=external_stream_id,
            policy=self.policy,
        )
        self.alerts = MacroblockingAlertPublisher(
            storage_id=storage_id,
            external_stream_id=external_stream_id,
            alert_keys=alert_keys,
            runtime_keys=runtime_keys,
            policy=self.policy,
            stream_max_length=alert_stream_max_length,
            alert_sink=alert_sink,
        )
        self.repository = RedisMacroblockingEventRepository(
            storage_id=storage_id,
            redis_client=self.redis,
            keys=self.keys,
            event_ttl_seconds=event_ttl_seconds,
            commit_ttl_seconds=commit_ttl_seconds,
            alerts=self.alerts,
        )
        self.recovery = RedisMacroblockingRecoveryRepository(
            storage_id=storage_id,
            redis_client=self.redis,
            keys=self.keys,
            ttl_seconds=event_ttl_seconds,
        )

    def apply(
        self,
        *,
        segment: Segment,
        result: MacroblockingDetectionResult,
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
            raise MacroblockingEventStateBusyError(
                f"Macroblocking state is busy for {segment.variant_id}"
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
        result: MacroblockingDetectionResult,
        commit_key: str,
    ) -> None:
        transitions = self.reducer.reduce(
            open_event=self.repository.load_open(segment.variant_stable_id),
            segment=segment,
            result=result,
        )
        recovery = self.recovery.load(segment.variant_stable_id)
        pipeline = self.redis.pipeline(transaction=True)
        commits_segment = False

        self.alerts.metric(pipeline, "macroblocking_analysis_total")
        if not result.checked or not result.coverage_complete:
            self.alerts.metric(pipeline, "macroblocking_invalid_total")
        if result.invalid_observation_count:
            self.alerts.metric(
                pipeline,
                "macroblocking_invalid_observation_total",
                result.invalid_observation_count,
            )
        if result.intervals:
            self.alerts.metric(
                pipeline,
                "macroblocking_candidate_interval_total",
                len(result.intervals),
            )

        for transition in transitions:
            commits_segment = commits_segment or transition.commits_segment
            if transition.type is MacroblockingEventTransitionType.MARK_COMMITTED:
                continue
            if transition.type is MacroblockingEventTransitionType.HEALTHY_EVIDENCE:
                recovery = self._apply_healthy(
                    pipeline, segment=segment, recovery=recovery
                )
                continue
            if transition.type is MacroblockingEventTransitionType.UNKNOWN_GAP:
                if recovery is not None:
                    recovery = replace(
                        recovery,
                        recovery_pending=True,
                        healthy_segments_observed=0,
                        last_observed_sequence=segment.sequence,
                        timeline_generation=segment.timeline_generation,
                        discontinuity_sequence=segment.discontinuity_sequence,
                    )
                    self.recovery.save(
                        pipeline, segment.variant_stable_id, recovery
                    )
                self.alerts.metric(
                    pipeline, "macroblocking_observation_gap_total"
                )
                continue
            if transition.event is None:
                raise RuntimeError("macroblocking transition has no event")

            event = transition.event
            if transition.type is MacroblockingEventTransitionType.PERSIST_OPEN:
                self.repository.queue_persist_open(pipeline, event)
            elif transition.type is MacroblockingEventTransitionType.OPEN_ALERT:
                if recovery is not None:
                    event.alert_sent = False
                self.repository.queue_persist_open(pipeline, event)
                if recovery is None:
                    self.alerts.add_event(
                        pipeline,
                        event=event,
                        state="OPEN",
                        reason="macroblocking_duration_threshold",
                    )
            elif transition.type is MacroblockingEventTransitionType.CLOSE_EVENT:
                self.repository.queue_close(pipeline, event)
                if event.alert_sent and recovery is None:
                    recovery = MacroblockingAlertRecoveryState(
                        alert_event_id=event.event_id,
                        recovery_pending=True,
                        healthy_segments_observed=0,
                        last_observed_sequence=event.end_sequence,
                        timeline_generation=event.timeline_generation,
                        discontinuity_sequence=event.discontinuity_sequence,
                    )
                    self.recovery.save(
                        pipeline, event.variant_stable_id, recovery
                    )

        if not commits_segment:
            raise RuntimeError("macroblocking reduction did not commit segment")
        self.repository.queue_commit(pipeline, commit_key)
        pipeline.execute()

    def _apply_healthy(
        self,
        pipeline,
        *,
        segment: Segment,
        recovery: MacroblockingAlertRecoveryState | None,
    ) -> MacroblockingAlertRecoveryState | None:
        if recovery is None:
            return None
        contiguous = (
            recovery.timeline_generation == segment.timeline_generation
            and recovery.discontinuity_sequence == segment.discontinuity_sequence
            and segment.sequence == recovery.last_observed_sequence + 1
        )
        healthy_count = (
            recovery.healthy_segments_observed + 1 if contiguous else 1
        )
        recovery = replace(
            recovery,
            recovery_pending=True,
            healthy_segments_observed=healthy_count,
            last_observed_sequence=segment.sequence,
            timeline_generation=segment.timeline_generation,
            discontinuity_sequence=segment.discontinuity_sequence,
        )
        if not self.policy.recovery_confirmed(healthy_count):
            self.recovery.save(pipeline, segment.variant_stable_id, recovery)
            return recovery

        event = self.repository.load_event(
            segment.variant_stable_id, recovery.alert_event_id
        )
        if event is None:
            raise RuntimeError("macroblocking recovery event is missing")
        event.status = MacroblockingEventStatus.RESOLVED
        event.resolution_reason = "video_returned"
        self.repository.queue_close(pipeline, event)
        self.alerts.add_event(
            pipeline,
            event=event,
            state="RESOLVED",
            reason="video_returned",
        )
        self.recovery.delete(pipeline, segment.variant_stable_id)
        return None

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4
import redis

from checks.black_screen.alert_publisher import BlackAlertPublisher
from core.alert_stream import AlertSink
from checks.black_screen.event_reducer import (
    BlackEventReducer,
    BlackEventTransitionType,
)
from checks.black_screen.event_repository import RedisBlackEventRepository
from checks.black_screen.redis_keys import BlackScreenRedisKeys
from checks.black_screen.redis_scripts import RELEASE_OWNED_LOCK
from checks.black_screen.recovery_repository import (
    RedisBlackAlertRecoveryRepository,
)
from checks.black_screen.repeated_reducer import (
    RepeatedAlertState,
    RepeatedBlackReducer,
    RepeatedBlackState,
)
from checks.black_screen.repeated_repository import (
    RedisRepeatedBlackRepository,
)
from core.redis_client import RedisClient, RedisUnavailableError
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from models.black_live import (
    BlackAlertRecoveryState,
    BlackAlertType,
    BlackEventStatus,
    BlackLiveEvent,
)
from models.detection import BlackDetectionResult
from models.segment import Segment
from policies.black_screen import BlackScreenAlertPolicy


class BlackEventStateBusyError(RuntimeError):
    pass


class RedisBlackEventStore:
    """Coordinates locking, domain reduction and persistence ports."""

    def __init__(
        self,
        *,
        storage_id: str,
        external_stream_id: str,
        redis_client: RedisClient,
        policy: BlackScreenAlertPolicy | None = None,
        black_keys: BlackScreenRedisKeys | None = None,
        alert_keys: AlertRedisKeys | None = None,
        runtime_keys: RuntimeRedisKeys | None = None,
        boundary_tolerance: float = 0.10,
        event_ttl_seconds: int = 86_400,
        commit_ttl_seconds: int = 21_600,
        event_lock_ms: int = 30_000,
        alert_stream_max_length: int = 10_000,
        alert_sink: AlertSink | None = None,
        reducer: BlackEventReducer | None = None,
        repeated_reducer: RepeatedBlackReducer | None = None,
        recovery_repository: RedisBlackAlertRecoveryRepository | None = None,
    ) -> None:
        self.storage_id = storage_id
        self.external_stream_id = external_stream_id
        self.redis = redis_client.client
        self.policy = policy or BlackScreenAlertPolicy()
        namespace = (
            black_keys.namespace if black_keys is not None else RedisNamespace()
        )
        self.keys = black_keys or BlackScreenRedisKeys(namespace)
        self.alert_keys = alert_keys or AlertRedisKeys(namespace)
        self.runtime_keys = runtime_keys or RuntimeRedisKeys(namespace)
        self.event_lock_ms = event_lock_ms
        self.reducer = reducer or BlackEventReducer(
            storage_id=storage_id,
            external_stream_id=external_stream_id,
            boundary_tolerance=boundary_tolerance,
        )
        alerts = BlackAlertPublisher(
            storage_id=storage_id,
            external_stream_id=external_stream_id,
            alert_keys=self.alert_keys,
            runtime_keys=self.runtime_keys,
            stream_max_length=alert_stream_max_length,
            alert_sink=alert_sink,
        )
        self.repository = RedisBlackEventRepository(
            storage_id=storage_id,
            redis_client=self.redis,
            black_keys=self.keys,
            event_ttl_seconds=event_ttl_seconds,
            commit_ttl_seconds=commit_ttl_seconds,
            alerts=alerts,
        )
        self.recovery = recovery_repository or RedisBlackAlertRecoveryRepository(
            storage_id=storage_id,
            redis_client=self.redis,
            black_keys=self.keys,
            ttl_seconds=event_ttl_seconds,
        )
        self.repeated_events = RedisRepeatedBlackRepository(
            storage_id=storage_id,
            redis_client=self.redis,
            policy=self.policy,
            black_keys=self.keys,
            event_ttl_seconds=event_ttl_seconds,
            commit_ttl_seconds=commit_ttl_seconds,
            reducer=repeated_reducer,
            alert_publisher=alerts,
        )

    def apply(
        self,
        segment: Segment,
        result: BlackDetectionResult,
    ) -> None:
        commit_key = self.keys.commit_marker(
            self.storage_id,
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
                self.storage_id, segment.variant_stable_id
            )
            token = uuid4().hex
            acquired = self.redis.set(
                lock_key, token, nx=True, px=self.event_lock_ms
            )
        except redis.RedisError as exc:
            raise RedisUnavailableError(str(exc)) from exc
        if not acquired:
            raise BlackEventStateBusyError(
                f"Black event state is busy for {segment.variant_id}"
            )

        try:
            if self.redis.exists(commit_key):
                return
            self._apply_locked(segment, result, commit_key)
        except redis.RedisError as exc:
            raise RedisUnavailableError(str(exc)) from exc
        finally:
            try:
                self.redis.eval(
                    RELEASE_OWNED_LOCK, 1, lock_key, token
                )
            except redis.RedisError:
                pass

    def _apply_locked(
        self,
        segment: Segment,
        result: BlackDetectionResult,
        commit_key: str,
    ) -> None:
        transitions = self.reducer.reduce(
            open_event=self.repository.load_open(
                segment.variant_stable_id
            ),
            segment=segment,
            result=result,
        )
        current_recovery = self.recovery.load(segment.variant_stable_id)
        pending_repeated_state: RepeatedBlackState | None = None
        pipe = self.redis.pipeline(transaction=True)

        for transition in transitions:
            transition_commit = (
                commit_key if transition.commits_segment else None
            )
            if transition.type == BlackEventTransitionType.MARK_COMMITTED:
                self.repository.mark_committed(commit_key, pipeline=pipe)

            elif transition.type == BlackEventTransitionType.PERSIST_OPEN:
                if transition.event is None:
                    raise RuntimeError("persist transition has no event")
                if current_recovery is not None:
                    if self._same_timeline(current_recovery, segment):
                        current_recovery = self._continue_pending_recovery(
                            current_recovery,
                            segment=segment,
                            pipeline=pipe,
                        )
                    else:
                        self._resolve_pending_recovery(
                            current_recovery,
                            segment=segment,
                            reason="timeline_discontinued",
                            pipeline=pipe,
                        )
                        self.recovery.delete(pipe, segment.variant_stable_id)
                        current_recovery = None
                self._persist_open(
                    transition.event,
                    transition_commit,
                    pipeline=pipe,
                    allow_alert=current_recovery is None,
                )

            elif transition.type in (
                BlackEventTransitionType.RESOLVE,
                BlackEventTransitionType.CLOSE_EVENT,
            ):
                if transition.event is None or transition.reason is None:
                    raise RuntimeError("close transition is incomplete")
                current_recovery, pending_repeated_state = self._close_event(
                    event=transition.event,
                    reason=transition.reason,
                    segment=segment,
                    current_recovery=current_recovery,
                    commit_key=transition_commit,
                    pipeline=pipe,
                )

            elif transition.type == BlackEventTransitionType.HEALTHY_EVIDENCE:
                current_recovery = self._handle_healthy_evidence(
                    segment=segment,
                    current_recovery=current_recovery,
                    commit_key=transition_commit,
                    pipeline=pipe,
                    pending_repeated_state=pending_repeated_state,
                )
                pending_repeated_state = None

            elif transition.type == BlackEventTransitionType.UNKNOWN_GAP:
                # Observation gap / decode failure: close unknown, never confirm recovery
                if transition.event is not None:
                    self.repository.close_unknown(
                        transition.event,
                        commit_key=transition_commit,
                        pipeline=pipe,
                    )
                    if (
                        current_recovery is None
                        and transition.event.long_alert_sent
                    ):
                        current_recovery = BlackAlertRecoveryState(
                            alert_event_id=transition.event.event_id,
                            alert_type=BlackAlertType.CONTINUOUS,
                            recovery_pending=True,
                            healthy_segments_observed=0,
                            last_observed_sequence=(
                                transition.event.end_sequence
                            ),
                            timeline_generation=(
                                transition.event.timeline_generation
                            ),
                            discontinuity_sequence=(
                                transition.event.discontinuity_sequence
                            ),
                        )
                        self.recovery.save(
                            pipe,
                            segment.variant_stable_id,
                            current_recovery,
                        )
                elif transition_commit:
                    self.repository.mark_committed(
                        transition_commit, pipeline=pipe
                    )
                if current_recovery is not None and transition.commits_segment:
                    current_recovery = self._handle_unknown_observation(
                        current_recovery,
                        segment=segment,
                        pipeline=pipe,
                    )

        pipe.execute()

    def _persist_open(
        self,
        event: BlackLiveEvent,
        commit_key: str | None,
        *,
        pipeline: Any,
        allow_alert: bool,
    ) -> None:
        should_alert = (
            allow_alert
            and
            not event.long_alert_sent
            and self.policy.should_alert_directly(event.duration)
        )
        if should_alert:
            event.long_alert_sent = True
        self.repository.persist_open(
            event,
            alert=should_alert,
            commit_key=commit_key,
            pipeline=pipeline,
        )

    def _close_event(
        self,
        *,
        event: BlackLiveEvent,
        reason: str,
        segment: Segment,
        current_recovery: BlackAlertRecoveryState | None,
        commit_key: str | None,
        pipeline: Any,
    ) -> tuple[BlackAlertRecoveryState | None, RepeatedBlackState | None]:
        event.status = BlackEventStatus.RESOLVED
        event.resolution_reason = reason
        event.detection_closed = True

        if (
            current_recovery is not None
            and not self._same_timeline(current_recovery, segment)
        ):
            self._resolve_pending_recovery(
                current_recovery,
                segment=segment,
                reason="timeline_discontinued",
                pipeline=pipeline,
            )
            self.recovery.delete(pipeline, segment.variant_stable_id)
            current_recovery = None

        if self.policy.is_repeated_candidate(
            event.duration,
            reference_segment_duration=event.reference_segment_duration,
        ):
            if (
                current_recovery is not None
                and current_recovery.alert_type is BlackAlertType.CONTINUOUS
            ):
                self.repository.close_canonical(
                    event, commit_key=commit_key, pipeline=pipeline
                )
                current_recovery = self._continue_pending_recovery(
                    current_recovery,
                    segment=segment,
                    pipeline=pipeline,
                    last_sequence=event.end_sequence,
                )
                return current_recovery, None

            reduction = self.repeated_events.record_resolved_event(
                event=event,
                payload=self.repository.encode(event),
                event_key=self.keys.event(
                    self.storage_id,
                    event.variant_stable_id,
                    event.event_id,
                ),
                open_key=self.keys.open_event(
                    self.storage_id, event.variant_stable_id
                ),
                commit_key=commit_key,
                pipeline=pipeline,
            )
            # If repeated OPEN alert was triggered, enter recovery_pending for repeated alert
            closed_seq = (
                event.end_sequence
                if event.end_sequence is not None
                else segment.sequence
            )
            alert = reduction.alert
            if alert is not None and alert.state is RepeatedAlertState.OPEN:
                current_recovery = BlackAlertRecoveryState(
                    alert_event_id=alert.event_id,
                    alert_type=BlackAlertType.REPEATED,
                    recovery_pending=True,
                    healthy_segments_observed=0,
                    last_observed_sequence=closed_seq,
                    timeline_generation=segment.timeline_generation,
                    discontinuity_sequence=segment.discontinuity_sequence,
                )
                self.recovery.save(
                    pipeline, segment.variant_stable_id, current_recovery
                )
            elif (
                current_recovery is not None
                and current_recovery.alert_type is BlackAlertType.REPEATED
            ):
                current_recovery = self._continue_pending_recovery(
                    current_recovery,
                    segment=segment,
                    pipeline=pipeline,
                    last_sequence=event.end_sequence,
                )
            return current_recovery, reduction.state

        # Check continuous alert
        alert_open = (
            current_recovery is None
            and
            not event.long_alert_sent
            and self.policy.should_alert_directly(event.duration)
        )
        if alert_open:
            event.long_alert_sent = True

        self.repository.close_canonical(
            event,
            alert_open=alert_open,
            commit_key=commit_key,
            pipeline=pipeline,
        )

        if current_recovery is not None:
            current_recovery = self._continue_pending_recovery(
                current_recovery,
                segment=segment,
                pipeline=pipeline,
                last_sequence=event.end_sequence,
            )
        elif event.long_alert_sent:
            # Continuous alert was sent (either earlier or right now at closure)
            # Transition to recovery_pending: do NOT emit RESOLVED immediately
            closed_seq = (
                event.end_sequence
                if event.end_sequence is not None
                else segment.sequence
            )
            current_recovery = BlackAlertRecoveryState(
                alert_event_id=event.event_id,
                alert_type=BlackAlertType.CONTINUOUS,
                recovery_pending=True,
                healthy_segments_observed=0,
                last_observed_sequence=closed_seq,
                timeline_generation=segment.timeline_generation,
                discontinuity_sequence=segment.discontinuity_sequence,
            )
            self.recovery.save(
                pipeline, segment.variant_stable_id, current_recovery
            )

        return current_recovery, None

    def _handle_healthy_evidence(
        self,
        *,
        segment: Segment,
        current_recovery: BlackAlertRecoveryState | None,
        commit_key: str | None,
        pipeline: Any,
        pending_repeated_state: RepeatedBlackState | None = None,
    ) -> BlackAlertRecoveryState | None:
        if commit_key:
            self.repository.mark_committed(commit_key, pipeline=pipeline)

        if current_recovery is None or not current_recovery.recovery_pending:
            return current_recovery

        # Validate continuity: timeline_generation, discontinuity_sequence, and sequence
        is_continuous = (
            self._same_timeline(current_recovery, segment)
            and (
                current_recovery.last_observed_sequence == -1
                or segment.sequence == current_recovery.last_observed_sequence + 1
            )
        )
        if not is_continuous:
            if not self._same_timeline(current_recovery, segment):
                self._resolve_pending_recovery(
                    current_recovery,
                    segment=segment,
                    reason="timeline_discontinued",
                    pipeline=pipeline,
                    repeated_state=pending_repeated_state,
                )
                self.recovery.delete(pipeline, segment.variant_stable_id)
                return None
            return self._continue_pending_recovery(
                current_recovery,
                segment=segment,
                pipeline=pipeline,
            )

        healthy_count = current_recovery.healthy_segments_observed + 1
        if healthy_count >= self.policy.recovery_healthy_segments:
            # Full healthy confirmation reached -> emit RESOLVED and clear recovery state
            self._resolve_pending_recovery(
                current_recovery,
                segment=segment,
                reason="healthy_segment_confirmed",
                pipeline=pipeline,
                repeated_state=pending_repeated_state,
            )

            self.recovery.delete(pipeline, segment.variant_stable_id)
            return None
        else:
            updated_recovery = BlackAlertRecoveryState(
                alert_event_id=current_recovery.alert_event_id,
                alert_type=current_recovery.alert_type,
                recovery_pending=True,
                healthy_segments_observed=healthy_count,
                last_observed_sequence=segment.sequence,
                timeline_generation=segment.timeline_generation,
                discontinuity_sequence=segment.discontinuity_sequence,
            )
            self.recovery.save(
                pipeline, segment.variant_stable_id, updated_recovery
            )
            return updated_recovery

    def _continue_pending_recovery(
        self,
        state: BlackAlertRecoveryState,
        *,
        segment: Segment,
        pipeline: Any,
        last_sequence: int | None = None,
    ) -> BlackAlertRecoveryState:
        updated = BlackAlertRecoveryState(
            alert_event_id=state.alert_event_id,
            alert_type=state.alert_type,
            recovery_pending=True,
            healthy_segments_observed=0,
            last_observed_sequence=(
                segment.sequence if last_sequence is None else last_sequence
            ),
            timeline_generation=segment.timeline_generation,
            discontinuity_sequence=segment.discontinuity_sequence,
        )
        self.recovery.save(pipeline, segment.variant_stable_id, updated)
        return updated

    def _handle_unknown_observation(
        self,
        state: BlackAlertRecoveryState,
        *,
        segment: Segment,
        pipeline: Any,
    ) -> BlackAlertRecoveryState | None:
        if not self._same_timeline(state, segment):
            self._resolve_pending_recovery(
                state,
                segment=segment,
                reason="timeline_discontinued",
                pipeline=pipeline,
            )
            self.recovery.delete(pipeline, segment.variant_stable_id)
            return None
        return self._continue_pending_recovery(
            state, segment=segment, pipeline=pipeline
        )

    def _resolve_pending_recovery(
        self,
        state: BlackAlertRecoveryState,
        *,
        segment: Segment,
        reason: str,
        pipeline: Any,
        repeated_state: RepeatedBlackState | None = None,
    ) -> None:
        if state.alert_type is BlackAlertType.CONTINUOUS:
            canonical_event = self.repository.load_event(
                segment.variant_stable_id, state.alert_event_id
            )
            if canonical_event is None:
                raise RuntimeError(
                    "continuous recovery references a missing canonical event"
                )
            self.repository.resolve_continuous_alert(
                event=canonical_event, reason=reason, pipeline=pipeline
            )
            return

        recovery_segment = segment
        if not self._same_timeline(state, segment):
            recovery_segment = replace(
                segment,
                timeline_generation=state.timeline_generation,
                discontinuity_sequence=state.discontinuity_sequence,
            )
        alert = self.repeated_events.resolve_confirmed_recovery(
            segment=recovery_segment,
            reason=reason,
            pipeline=pipeline,
            state=repeated_state,
        )
        if alert is None:
            raise RuntimeError(
                "repeated recovery references a missing incident"
            )

    @staticmethod
    def _same_timeline(
        state: BlackAlertRecoveryState,
        segment: Segment,
    ) -> bool:
        return (
            state.timeline_generation == segment.timeline_generation
            and state.discontinuity_sequence
            == segment.discontinuity_sequence
        )

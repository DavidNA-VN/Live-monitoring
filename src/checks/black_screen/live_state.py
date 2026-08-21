from __future__ import annotations

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
from checks.black_screen.repeated_reducer import RepeatedBlackReducer
from checks.black_screen.repeated_repository import (
    RedisRepeatedBlackRepository,
)
from core.redis_client import RedisClient, RedisUnavailableError
from core.redis_keys import AlertRedisKeys, RedisNamespace, RuntimeRedisKeys
from models.black_live import BlackEventStatus, BlackLiveEvent
from models.detection import BlackDetectionResult
from models.segment import Segment
from policies.black_screen import BlackScreenAlertPolicy


class BlackEventStateBusyError(RuntimeError):
    pass


class RedisBlackEventStore:
    """Coordinates locking, domain reduction and persistence ports."""

    def __init__(
        self,
        stream_id: str,
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
    ) -> None:
        self.stream_id = stream_id
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
            stream_id=stream_id,
            boundary_tolerance=boundary_tolerance,
        )
        alerts = BlackAlertPublisher(
            stream_id=stream_id,
            alert_keys=self.alert_keys,
            runtime_keys=self.runtime_keys,
            stream_max_length=alert_stream_max_length,
            alert_sink=alert_sink,
        )
        self.repository = RedisBlackEventRepository(
            stream_id=stream_id,
            redis_client=self.redis,
            black_keys=self.keys,
            event_ttl_seconds=event_ttl_seconds,
            commit_ttl_seconds=commit_ttl_seconds,
            alerts=alerts,
        )
        self.repeated_events = RedisRepeatedBlackRepository(
            stream_id=stream_id,
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
                self.stream_id, segment.variant_stable_id
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
            self.repeated_events.resolve_if_quiet(segment)
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
        for transition in transitions:
            transition_commit = (
                commit_key if transition.commits_segment else None
            )
            if transition.type == BlackEventTransitionType.MARK_COMMITTED:
                self.repository.mark_committed(commit_key)
            elif transition.type == BlackEventTransitionType.PERSIST_OPEN:
                if transition.event is None:
                    raise RuntimeError("persist transition has no event")
                self._persist_open(transition.event, transition_commit)
            elif transition.type == BlackEventTransitionType.RESOLVE:
                if transition.event is None or transition.reason is None:
                    raise RuntimeError("resolve transition is incomplete")
                self._resolve(
                    transition.event,
                    transition.reason,
                    transition_commit,
                )

    def _persist_open(
        self,
        event: BlackLiveEvent,
        commit_key: str | None,
    ) -> None:
        should_alert = (
            not event.long_alert_sent
            and self.policy.should_alert_directly(event.duration)
        )
        if should_alert:
            event.long_alert_sent = True
        self.repository.persist_open(
            event,
            alert=should_alert,
            commit_key=commit_key,
        )

    def _resolve(
        self,
        event: BlackLiveEvent,
        reason: str,
        commit_key: str | None,
    ) -> None:
        event.status = BlackEventStatus.RESOLVED
        event.resolution_reason = reason
        if self.policy.is_repeated_candidate(event.duration):
            self.repeated_events.record_resolved_event(
                event=event,
                payload=self.repository.encode(event),
                event_key=self.keys.event(
                    self.stream_id, event.event_id
                ),
                open_key=self.keys.open_event(
                    self.stream_id, event.variant_stable_id
                ),
                commit_key=commit_key,
            )
            return

        alert_on_resolution = (
            not event.long_alert_sent
            and self.policy.should_alert_directly(event.duration)
        )
        if alert_on_resolution:
            event.long_alert_sent = True
        self.repository.resolve_long(
            event,
            reason=reason,
            alert_on_resolution=alert_on_resolution,
            commit_key=commit_key,
        )

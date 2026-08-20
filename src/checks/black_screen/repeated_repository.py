from datetime import datetime, timezone

import redis

from checks.black_screen.repeated_reducer import (
    RepeatedBlackIncident,
    RepeatedBlackReducer,
    RepeatedBlackState,
    ShortBlackRecord,
)
from checks.black_screen.alert_publisher import BlackAlertPublisher
from core.redis_client import RedisUnavailableError
from core.redis_keys import RedisKeyBuilder
from models.black_live import BlackLiveEvent
from models.segment import Segment
from policies.black_screen import BlackScreenAlertPolicy


class RedisRepeatedBlackRepository:
    def __init__(
        self,
        *,
        stream_id: str,
        redis_client,
        policy: BlackScreenAlertPolicy,
        key_builder: RedisKeyBuilder,
        event_ttl_seconds: int,
        commit_ttl_seconds: int,
        reducer: RepeatedBlackReducer | None = None,
        alert_publisher: BlackAlertPublisher | None = None,
    ) -> None:
        self.stream_id = stream_id
        self.redis = redis_client
        self.policy = policy
        self.keys = key_builder
        self.event_ttl_seconds = event_ttl_seconds
        self.commit_ttl_seconds = commit_ttl_seconds
        self.reducer = reducer or RepeatedBlackReducer(policy)
        self.alerts = alert_publisher or BlackAlertPublisher(
            stream_id=stream_id,
            key_builder=self.keys,
        )

    def record_resolved_event(
        self,
        *,
        event: BlackLiveEvent,
        payload: str,
        event_key: str,
        open_key: str,
        commit_key: str | None,
    ) -> None:
        history_key, duration_key, incident_key = (
            self._state_keys(event.variant_stable_id)
        )
        event_time = event.end_program_time or datetime.now(
            timezone.utc
        )
        history_ttl = max(
            1,
            int(self.policy.repeated_window * 2),
        )
        incident_ttl = max(
            self.event_ttl_seconds,
            int(self.policy.repeated_recovery_window * 2),
        )

        state = self._load_state(
            history_key=history_key,
            duration_key=duration_key,
            incident_key=incident_key,
        )
        reduction = self.reducer.record_short_event(
            state=state,
            record=ShortBlackRecord(
                event_id=event.event_id,
                event_at=event_time.timestamp(),
                duration=event.duration,
            ),
        )
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.set(
            event_key,
            payload,
            ex=self.event_ttl_seconds,
        )
        pipeline.delete(open_key)
        self._write_state(
            pipeline=pipeline,
            state=reduction.state,
            history_key=history_key,
            duration_key=duration_key,
            incident_key=incident_key,
            history_ttl=history_ttl,
            incident_ttl=incident_ttl,
        )

        if reduction.alert is not None:
            self.alerts.add_repeated(
                pipeline,
                alert=reduction.alert,
                variant_id=event.variant_id,
                policy=self.policy,
            )
        if commit_key is not None:
            pipeline.set(
                commit_key,
                "1",
                ex=self.commit_ttl_seconds,
            )

        try:
            pipeline.execute()
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                "Unable to atomically resolve short black event "
                f"{event.event_id}: {exc}"
            ) from exc

    def resolve_if_quiet(self, segment: Segment) -> None:
        reference_time = (
            segment.program_date_time
            or datetime.now(timezone.utc)
        )
        history_key, duration_key, incident_key = (
            self._state_keys(segment.variant_stable_id)
        )

        try:
            state = self._load_state(
                history_key=history_key,
                duration_key=duration_key,
                incident_key=incident_key,
            )
            reduction = self.reducer.resolve_if_quiet(
                state=state,
                reference_time=reference_time.timestamp(),
            )
            if not reduction.clear_state:
                return

            pipeline = self.redis.pipeline(transaction=True)
            if reduction.alert is not None:
                self.alerts.add_repeated(
                    pipeline,
                    alert=reduction.alert,
                    variant_id=segment.variant_id,
                    policy=self.policy,
                )
            pipeline.delete(
                incident_key,
                history_key,
                duration_key,
            )
            pipeline.execute()
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Unable to resolve repeated black incident: {exc}"
            ) from exc

    def _state_keys(self, variant_stable_id: str):
        return (
            self.keys.black_short_history(
                stream_id=self.stream_id,
                variant_stable_id=variant_stable_id,
            ),
            self.keys.black_short_duration(
                stream_id=self.stream_id,
                variant_stable_id=variant_stable_id,
            ),
            self.keys.black_repeat_incident(
                stream_id=self.stream_id,
                variant_stable_id=variant_stable_id,
            ),
        )

    def _load_state(
        self,
        *,
        history_key: str,
        duration_key: str,
        incident_key: str,
    ) -> RepeatedBlackState:
        raw_history = self.redis.zrange(
            history_key,
            0,
            -1,
            withscores=True,
        )
        event_ids = [item[0] for item in raw_history]
        raw_durations = (
            self.redis.hmget(duration_key, event_ids)
            if event_ids
            else []
        )
        history = tuple(
            ShortBlackRecord(
                event_id=event_id,
                event_at=float(event_at),
                duration=float(raw_duration or 0),
            )
            for (event_id, event_at), raw_duration in zip(
                raw_history,
                raw_durations,
            )
        )
        raw_incident = self.redis.hgetall(incident_key)
        incident = None

        if raw_incident:
            incident = RepeatedBlackIncident(
                incident_id=raw_incident["incident_id"],
                first_event_id=raw_incident["first_event_id"],
                latest_event_id=raw_incident["latest_event_id"],
                first_event_at=float(raw_incident["first_event_at"]),
                last_event_at=float(raw_incident["last_event_at"]),
                occurrences=int(raw_incident["occurrences"]),
                total_black_duration=float(
                    raw_incident["total_black_duration"]
                ),
                last_notified_occurrences=int(
                    raw_incident["last_notified_occurrences"]
                ),
            )

        return RepeatedBlackState(
            history=history,
            incident=incident,
        )

    @staticmethod
    def _write_state(
        *,
        pipeline,
        state: RepeatedBlackState,
        history_key: str,
        duration_key: str,
        incident_key: str,
        history_ttl: int,
        incident_ttl: int,
    ) -> None:
        pipeline.delete(history_key, duration_key, incident_key)

        if state.history:
            pipeline.zadd(
                history_key,
                {
                    item.event_id: item.event_at
                    for item in state.history
                },
            )
            pipeline.hset(
                duration_key,
                mapping={
                    item.event_id: f"{item.duration:.6f}"
                    for item in state.history
                },
            )
            pipeline.expire(history_key, history_ttl)
            pipeline.expire(duration_key, history_ttl)

        if state.incident is not None:
            incident = state.incident
            pipeline.hset(
                incident_key,
                mapping={
                    "incident_id": incident.incident_id,
                    "status": "open",
                    "first_event_id": incident.first_event_id,
                    "latest_event_id": incident.latest_event_id,
                    "first_event_at": incident.first_event_at,
                    "last_event_at": incident.last_event_at,
                    "occurrences": incident.occurrences,
                    "total_black_duration": (
                        incident.total_black_duration
                    ),
                    "last_notified_occurrences": (
                        incident.last_notified_occurrences
                    ),
                },
            )
            pipeline.expire(incident_key, incident_ttl)

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

import redis

from checks.black_screen.repeated_reducer import (
    RepeatedBlackAlert,
    RepeatedBlackIncident,
    RepeatedBlackReduction,
    RepeatedBlackReducer,
    RepeatedBlackState,
    ShortBlackRecord,
)
from checks.black_screen.alert_publisher import BlackAlertPublisher
from core.redis_client import RedisUnavailableError
from checks.black_screen.redis_keys import BlackScreenRedisKeys
from models.black_live import BlackLiveEvent
from models.segment import Segment
from policies.black_screen import BlackScreenAlertPolicy


class RedisRepeatedBlackRepository:
    def __init__(
        self,
        *,
        storage_id: str,
        redis_client,
        policy: BlackScreenAlertPolicy,
        black_keys: BlackScreenRedisKeys,
        event_ttl_seconds: int,
        commit_ttl_seconds: int,
        alert_publisher: BlackAlertPublisher,
        reducer: RepeatedBlackReducer | None = None,
    ) -> None:
        self.storage_id = storage_id
        self.redis = redis_client
        self.policy = policy
        self.keys = black_keys
        self.event_ttl_seconds = event_ttl_seconds
        self.commit_ttl_seconds = commit_ttl_seconds
        self.reducer = reducer or RepeatedBlackReducer(policy)
        self.alerts = alert_publisher

    def record_resolved_event(
        self,
        *,
        event: BlackLiveEvent,
        payload: str,
        event_key: str,
        open_key: str,
        commit_key: str | None,
        pipeline: Any = None,
    ) -> RepeatedBlackReduction:
        history_key, duration_key, incident_key = (
            self._state_keys(
                event.variant_stable_id,
                event.timeline_generation,
            )
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
            int(self.policy.repeated_window * 2),
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
                start_sequence=event.start_sequence,
                end_sequence=event.end_sequence,
                start_segment_uri=event.start_segment_uri,
                end_segment_uri=event.end_segment_uri,
                affected_segment_count=len(event.affected_segments),
            ),
        )
        owns_pipe = pipeline is None
        pipe = self.redis.pipeline(transaction=True) if owns_pipe else pipeline
        pipe.set(
            event_key,
            payload,
            ex=self.event_ttl_seconds,
        )
        pipe.delete(open_key)
        self._write_state(
            pipeline=pipe,
            state=reduction.state,
            history_key=history_key,
            duration_key=duration_key,
            incident_key=incident_key,
            history_ttl=history_ttl,
            incident_ttl=incident_ttl,
        )

        if reduction.alert is not None:
            self.alerts.add_repeated(
                pipe,
                alert=reduction.alert,
                variant_id=event.variant_id,
                variant_stable_id=event.variant_stable_id,
                policy=self.policy,
            )
        if commit_key is not None:
            pipe.set(
                commit_key,
                "1",
                ex=self.commit_ttl_seconds,
            )

        if owns_pipe:
            try:
                pipe.execute()
            except redis.RedisError as exc:
                raise RedisUnavailableError(
                    "Unable to atomically resolve short black event "
                    f"{event.event_id}: {exc}"
                ) from exc

        return reduction

    def resolve_confirmed_recovery(
        self,
        *,
        segment: Segment,
        reason: str = "healthy_segment_confirmed",
        pipeline: Any = None,
        state: RepeatedBlackState | None = None,
    ) -> RepeatedBlackAlert | None:
        history_key, duration_key, incident_key = (
            self._state_keys(
                segment.variant_stable_id,
                segment.timeline_generation,
            )
        )
        try:
            if state is None:
                state = self._load_state(
                    history_key=history_key,
                    duration_key=duration_key,
                    incident_key=incident_key,
                )
            if state.incident is None:
                return None

            reduction = self.reducer.resolve_incident(
                state=state,
                reason=reason,
            )
            owns_pipe = pipeline is None
            pipe = self.redis.pipeline(transaction=True) if owns_pipe else pipeline
            if reduction.alert is not None:
                self.alerts.add_repeated(
                    pipe,
                    alert=reduction.alert,
                    variant_id=segment.variant_id,
                    variant_stable_id=segment.variant_stable_id,
                    policy=self.policy,
                )
            pipe.delete(
                incident_key,
                history_key,
                duration_key,
            )
            if owns_pipe:
                pipe.execute()
            return reduction.alert
        except redis.RedisError as exc:
            raise RedisUnavailableError(
                f"Unable to resolve confirmed repeated black recovery: {exc}"
            ) from exc

    def _state_keys(
        self,
        variant_stable_id: str,
        timeline_generation: int,
    ):
        return (
            self.keys.short_history(
                stream_id=self.storage_id,
                variant_stable_id=variant_stable_id,
                timeline_generation=timeline_generation,
            ),
            self.keys.short_duration(
                stream_id=self.storage_id,
                variant_stable_id=variant_stable_id,
                timeline_generation=timeline_generation,
            ),
            self.keys.repeat_incident(
                stream_id=self.storage_id,
                variant_stable_id=variant_stable_id,
                timeline_generation=timeline_generation,
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
            self._decode_record(event_id, float(event_at), raw_duration)
            for (event_id, event_at), raw_duration in zip(raw_history, raw_durations)
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
                start_sequence=int(raw_incident.get("start_sequence", -1)),
                end_sequence=int(raw_incident.get("end_sequence", -1)),
                start_segment_uri=raw_incident.get("start_segment_uri", ""),
                end_segment_uri=raw_incident.get("end_segment_uri", ""),
                affected_segment_count=int(raw_incident.get("affected_segment_count", 0)),
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
                    item.event_id: json.dumps({
                        "duration": item.duration,
                        "start_sequence": item.start_sequence,
                        "end_sequence": item.end_sequence,
                        "start_segment_uri": item.start_segment_uri,
                        "end_segment_uri": item.end_segment_uri,
                        "affected_segment_count": item.affected_segment_count,
                    }, separators=(",", ":"))
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
                    "start_sequence": incident.start_sequence,
                    "end_sequence": incident.end_sequence,
                    "start_segment_uri": incident.start_segment_uri,
                    "end_segment_uri": incident.end_segment_uri,
                    "affected_segment_count": incident.affected_segment_count,
                },
            )
            pipeline.expire(incident_key, incident_ttl)

    @staticmethod
    def _decode_record(event_id, event_at, raw):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
        if not isinstance(data, dict):
            return ShortBlackRecord(event_id, event_at, float(raw or 0))
        return ShortBlackRecord(
            event_id=event_id,
            event_at=event_at,
            duration=float(data.get("duration", 0)),
            start_sequence=int(data.get("start_sequence", -1)),
            end_sequence=int(data.get("end_sequence", -1)),
            start_segment_uri=str(data.get("start_segment_uri", "")),
            end_segment_uri=str(data.get("end_segment_uri", "")),
            affected_segment_count=int(data.get("affected_segment_count", 0)),
        )

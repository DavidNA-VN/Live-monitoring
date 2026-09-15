from __future__ import annotations

from datetime import datetime, timezone
import json

from checks.video_freeze.repeated_reducer import RepeatedFreezeReducer
from checks.video_freeze.repeated_state import FreezeWarningRecord, RepeatedFreezeIncident, RepeatedFreezeState


class RedisRepeatedFreezeRepository:
    def __init__(self, *, storage_id, redis_client, policy, freeze_keys,
                 event_ttl_seconds, alerts, reducer=None):
        self.storage_id, self.redis, self.policy = storage_id, redis_client, policy
        self.keys, self.event_ttl_seconds, self.alerts = freeze_keys, event_ttl_seconds, alerts
        self.reducer = reducer or RepeatedFreezeReducer(policy)

    def record_closed_event(self, *, event, pipeline):
        keys = self._keys(event.variant_stable_id, event.timeline_generation)
        at = (event.end_program_time or datetime.now(timezone.utc)).timestamp()
        state = self._load(*keys, minimum=at - self.policy.repeated_window)
        result = self.reducer.record_candidate(
            state=state, record=FreezeWarningRecord(
                event.event_id, at, event.duration, event.start_sequence,
                event.end_sequence, event.start_segment_uri,
                event.end_segment_uri, event.affected_segment_count,
            )
        )
        self._save(pipeline, keys, result.state, result.clear_state)
        if result.alert:
            self.alerts.add_repeated(pipeline, alert=result.alert,
                variant_id=event.variant_id, variant_stable_id=event.variant_stable_id)
        return result

    def resolve_confirmed_recovery(self, *, segment, timeline_generation, reason, pipeline, state=None):
        keys = self._keys(segment.variant_stable_id, timeline_generation)
        result = self.reducer.resolve_incident(
            state=(state or self._load(*keys, minimum=float("-inf"))), reason=reason
        )
        if not result.alert:
            return None
        self._save(pipeline, keys, result.state, result.clear_state)
        self.alerts.add_repeated(pipeline, alert=result.alert,
            variant_id=segment.variant_id, variant_stable_id=segment.variant_stable_id)
        return result.alert

    def _keys(self, variant, timeline):
        return (self.keys.short_history(self.storage_id, variant, timeline),
                self.keys.short_duration(self.storage_id, variant, timeline),
                self.keys.repeat_incident(self.storage_id, variant, timeline))

    def _load(self, history_key, duration_key, incident_key, *, minimum):
        raw = self.redis.zrangebyscore(history_key, minimum, "+inf", withscores=True)
        ids = [item[0] for item in raw]
        durations = self.redis.hmget(duration_key, ids) if ids else []
        history = tuple(self._decode_record(event_id, float(score), duration)
                        for (event_id, score), duration in zip(raw, durations))
        data = self.redis.hgetall(incident_key)
        incident = None if not data else RepeatedFreezeIncident(
            incident_id=data["incident_id"], first_event_id=data["first_event_id"],
            latest_event_id=data["latest_event_id"], first_event_at=float(data["first_event_at"]),
            last_event_at=float(data["last_event_at"]), occurrences=int(data["occurrences"]),
            total_duration=float(data["total_duration"]),
            last_notified_occurrences=int(data.get("last_notified_occurrences", data["occurrences"])))
        if incident is not None:
            incident = RepeatedFreezeIncident(
                **{**incident.__dict__,
                   "start_sequence": int(data.get("start_sequence", -1)),
                   "end_sequence": int(data.get("end_sequence", -1)),
                   "start_segment_uri": data.get("start_segment_uri", ""),
                   "end_segment_uri": data.get("end_segment_uri", ""),
                   "affected_segment_count": int(data.get("affected_segment_count", 0))}
            )
        return RepeatedFreezeState(history, incident)

    def _save(self, pipe, keys, state, clear):
        history_key, duration_key, incident_key = keys
        pipe.delete(history_key, duration_key, incident_key)
        if clear:
            return
        ttl = max(1, int(self.policy.repeated_window * 2))
        if state.history:
            pipe.zadd(history_key, {item.event_id: item.event_at for item in state.history})
            pipe.hset(duration_key, mapping={item.event_id: json.dumps({
                "duration": item.duration, "start_sequence": item.start_sequence,
                "end_sequence": item.end_sequence, "start_segment_uri": item.start_segment_uri,
                "end_segment_uri": item.end_segment_uri,
                "affected_segment_count": item.affected_segment_count,
            }, separators=(",", ":")) for item in state.history})
            pipe.expire(history_key, ttl)
            pipe.expire(duration_key, ttl)
        if state.incident:
            item = state.incident
            pipe.hset(incident_key, mapping={
                "incident_id": item.incident_id, "first_event_id": item.first_event_id,
                "latest_event_id": item.latest_event_id, "first_event_at": item.first_event_at,
                "last_event_at": item.last_event_at, "occurrences": item.occurrences,
                "total_duration": item.total_duration,
                "last_notified_occurrences": item.last_notified_occurrences})
            pipe.hset(incident_key, mapping={
                "start_sequence": item.start_sequence,
                "end_sequence": item.end_sequence,
                "start_segment_uri": item.start_segment_uri,
                "end_segment_uri": item.end_segment_uri,
                "affected_segment_count": item.affected_segment_count,
            })
            pipe.expire(incident_key, max(self.event_ttl_seconds, ttl))

    @staticmethod
    def _decode_record(event_id, event_at, raw):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
        if not isinstance(data, dict):
            return FreezeWarningRecord(event_id, event_at, float(raw or 0))
        return FreezeWarningRecord(
            event_id, event_at, float(data.get("duration", 0)),
            int(data.get("start_sequence", -1)), int(data.get("end_sequence", -1)),
            str(data.get("start_segment_uri", "")), str(data.get("end_segment_uri", "")),
            int(data.get("affected_segment_count", 0)),
        )
